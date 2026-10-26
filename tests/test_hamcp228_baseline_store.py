"""HAMCP-228: durable baseline store, resume, compare_automation_baselines.

Synthetic only: the fake Home Assistant client and Core runtime come from
``test_automation_baseline_capture``; every store lives under TMPDIR.
"""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]

import hamcp226_credential_cases as cases  # noqa: E402
from test_automation_baseline_capture import SECRET, Client, Core, fixture  # noqa: E402
from ha_mcp_engineering import untrusted_text  # noqa: E402
from ha_mcp_engineering.audit_baseline import capture_contracts as c  # noqa: E402
from ha_mcp_engineering.audit_baseline import compare_baselines  # noqa: E402
from ha_mcp_engineering.audit_baseline.capture_provider import BaselineCaptureProvider, Projector  # noqa: E402
from ha_mcp_engineering.audit_baseline.capture_service import BaselineCaptureService  # noqa: E402
from ha_mcp_engineering.audit_baseline.comparison import compare_recorded_fingerprints  # noqa: E402
from ha_mcp_engineering.audit_baseline.models import FINGERPRINT_MODEL  # noqa: E402
from ha_mcp_engineering.audit_baseline.store import BaselineStore, StoreError  # noqa: E402
from ha_mcp_engineering.audit_baseline.validation import (  # noqa: E402
    BaselineValidationError, baseline_from_value)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.sanitization import sanitize_untrusted_data  # noqa: E402

OWNER = 'synthetic-owner'
DAY_MS = 86_400_000
KNOWN = cases.KNOWN_SECRET


def capture_id(page):
    return page['snapshot']['capture_id']


class Harness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = OWNER
        self.telemetry.core_dispatch_authorizer = lambda: True
        self.core = Core()
        self.now = 1_800_000_000.0
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.governance = Path(directory.name)

    def tearDown(self):
        end_request(self.token)

    def service(self, data=None, **kwargs):
        client = Client(fixture() if data is None else data)
        service = BaselineCaptureService(
            BaselineCaptureProvider(client, self.core, known_secrets=(SECRET, KNOWN)),
            BaselineStore(self.governance), wall=lambda: self.now, **kwargs)
        return service, client

    async def capture(self, service, **kwargs):
        return await service.capture(limit=100, **kwargs)

    async def reason(self, awaitable):
        with self.assertRaises(c.CaptureError) as caught:
            await awaitable
        return caught.exception

    def entries(self, service):
        return service.store.load_index()['baselines']

    def record_path(self, service, key):
        return service.store.records / (key + '.json')


class StoreTests(Harness):
    async def stored_record(self):
        service, _ = self.service()
        key = capture_id(await self.capture(service))
        entry = self.entries(service)[0]
        return service, key, entry, self.record_path(service, key).read_bytes()

    def put(self, store, key, raw, entry, now_ms, caller=OWNER):
        return store.put(capture_id=key, caller=caller, encoded=raw, artifact_sha256=entry['artifact_sha256'],
                         baseline_id=entry['baseline_id'], resumed_from=None, now_ms=now_ms)

    async def test_namespace_private_modes_and_only_the_report_is_stored(self):
        service, key, entry, raw = await self.stored_record()
        root = self.governance / 'audit-baselines-v1'
        self.assertEqual(service.store.root, root)
        for path, mode in ((root, 0o700), (root / 'records', 0o700), (root / 'index.json', 0o600),
                           (root / 'records' / (key + '.json'), 0o600)):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode, path)
        self.assertEqual(sorted(p.name for p in (root / 'records').iterdir()), [key + '.json'])
        self.assertEqual(set(json.loads(raw)), {'baseline', 'diagnostics'})
        self.assertNotIn(SECRET.encode(), raw)
        self.assertEqual(entry['bytes'], len(raw))
        self.assertEqual(entry['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(entry['caller'], OWNER)
        self.assertIsNone(entry['resumed_from'])

    async def test_a_capture_that_does_not_finish_writes_nothing(self):
        data = fixture()
        data['principal']['is_admin'] = False
        service, _ = self.service(data)
        self.assertEqual((await self.reason(self.capture(service))).reason, 'access_denied')
        self.assertEqual(self.entries(service), [])
        self.assertEqual(list(service.store.records.iterdir()), [])

    async def test_capacity_refuses_before_any_read_with_oldest_age_and_never_evicts(self):
        service, key, entry, raw = await self.stored_record()
        first_ms = entry['created_at_ms']
        for index in range(1, c.STORE_BASELINES):
            self.put(service.store, f'{index:032x}', raw, entry, first_ms + index * 1000)
        ids = [item['capture_id'] for item in self.entries(service)]
        self.assertEqual(len(ids), 12)
        with self.assertRaises(StoreError) as caught:
            self.put(service.store, 'f' * 32, raw, entry, first_ms + 3_600_000)
        self.assertEqual((caught.exception.reason, caught.exception.oldest_age_seconds), ('store_full', 3600))
        self.now += 7200
        client = service.provider.client
        before = client.requests
        error = await self.reason(self.capture(service))
        self.assertEqual(error.reason, 'store_full')
        self.assertEqual(error.details, {'reason': 'store_full', 'stored_baselines': 12,
                                         'oldest_age_seconds': 7200, 'retention_seconds': 180 * 86_400})
        self.assertFalse(error.retryable)
        self.assertEqual(client.requests, before)
        self.assertEqual([item['capture_id'] for item in self.entries(service)], ids)
        self.assertIsNotNone(service.store.get(key, OWNER, first_ms + 1)[0])

    async def test_retention_sweep_expires_records_temporaries_and_orphans(self):
        service, key, entry, raw = await self.stored_record()
        records = service.store.records
        (records / '.pending-abandoned').write_bytes(b'x')
        (records / ('e' * 32 + '.json')).write_bytes(b'orphan')
        created = entry['created_at_ms']
        service.store.get(key, OWNER, created + 1)
        self.assertEqual(sorted(p.name for p in records.iterdir()), [key + '.json'])
        horizon = created + c.STORE_RETENTION_SECONDS * 1000
        service.store.get(key, OWNER, horizon - 1)
        with self.assertRaises(StoreError) as caught:
            service.store.get(key, OWNER, horizon)
        self.assertEqual(caught.exception.reason, 'baseline_not_found')
        self.assertEqual(self.entries(service), [])
        self.assertEqual(list(records.iterdir()), [])

    async def test_damage_quarantines_with_bounded_content_free_stubs(self):
        service, key, entry, raw = await self.stored_record()
        now_ms = entry['created_at_ms'] + 1
        path = self.record_path(service, key)
        path.write_bytes(raw[:-1] + b' ')
        with self.assertRaises(StoreError) as caught:
            service.store.get(key, OWNER, now_ms)
        self.assertEqual(caught.exception.reason, 'baseline_damaged')
        self.assertFalse(path.exists())
        index = service.store.load_index()
        self.assertEqual(index['baselines'], [])
        self.assertEqual(index['quarantine']['entries'], [{
            'capture_id': key, 'quarantined_at_ms': now_ms, 'reason': 'digest_mismatch',
            'observed_bytes': len(raw), 'observed_sha256': hashlib.sha256(raw[:-1] + b' ').hexdigest()}])
        # A damaged record never reaches compare or resume.
        self.assertEqual((await self.reason(service.compare(earlier_capture_id=key, later_capture_id=key))).reason,
                         'baseline_not_found')

        for reason, damage in (('missing', lambda p: p.unlink()),
                               ('unreadable', lambda p: (p.unlink(), p.symlink_to('/dev/null')))):
            other = f'{len(reason):032x}'
            self.put(service.store, other, raw, entry, now_ms)
            damage(self.record_path(service, other))
            with self.assertRaises(StoreError) as caught:
                service.store.get(other, OWNER, now_ms)
            self.assertEqual(caught.exception.reason, 'baseline_damaged')
            self.assertEqual(service.store.load_index()['quarantine']['entries'][-1]['reason'], reason)

        # Self-consistent bytes that are not a valid baseline report.
        bogus = c.canonical({'baseline': {'schema': 'x'}, 'diagnostics': {'record_outcomes': {}}})
        self.put(service.store, 'd' * 32, bogus, entry, now_ms)
        with self.assertRaises(StoreError):
            service.store.get('d' * 32, OWNER, now_ms)
        stub = service.store.load_index()['quarantine']['entries'][-1]
        self.assertEqual((stub['reason'], stub['observed_bytes']), ('invalid_record', len(bogus)))

        index = service.store.load_index()
        index['quarantine']['entries'] = [dict(stub, capture_id=f'{n:032x}') for n in range(c.QUARANTINE_MAX)]
        service.store._write_index(index)
        self.put(service.store, 'c' * 32, raw, entry, now_ms)
        self.record_path(service, 'c' * 32).unlink()
        with self.assertRaises(StoreError):
            service.store.get('c' * 32, OWNER, now_ms)
        quarantine = service.store.load_index()['quarantine']
        self.assertEqual((len(quarantine['entries']), quarantine['dropped']), (c.QUARANTINE_MAX, 1))
        self.assertEqual(quarantine['entries'][-1]['capture_id'], 'c' * 32)

    async def test_caller_binding_and_anonymous_refusal(self):
        service, key, entry, raw = await self.stored_record()
        now_ms = entry['created_at_ms'] + 1
        with self.assertRaises(StoreError) as caught:
            service.store.get(key, 'synthetic-other', now_ms)
        self.assertEqual(caught.exception.reason, 'baseline_not_found')
        for caller in ('anonymous', '', 'x' * 257, 'bad\ncaller'):
            with self.assertRaises(StoreError) as caught:
                self.put(service.store, 'a' * 32, raw, entry, now_ms, caller=caller)
            self.assertEqual(caught.exception.reason, 'store_invalid')
        with self.assertRaises(StoreError):
            self.put(service.store, key, raw, entry, now_ms)
        for bad in ('A' * 32, 'a' * 31, '../' + 'a' * 29, 7):
            with self.assertRaises(StoreError) as caught:
                service.store.get(bad, OWNER, now_ms)
            self.assertEqual(caught.exception.reason, 'baseline_not_found')

    async def test_symlinked_namespace_index_and_invalid_index_refused(self):
        service, key, entry, raw = await self.stored_record()
        now_ms = entry['created_at_ms'] + 1
        index = service.store.index_path
        saved = index.read_bytes()
        index.unlink()
        target = self.governance / 'elsewhere.json'
        target.write_bytes(saved)
        index.symlink_to(target)
        with self.assertRaises(StoreError) as caught:
            service.store.get(key, OWNER, now_ms)
        self.assertEqual(caught.exception.reason, 'store_invalid')
        index.unlink()
        for garbage in (b'not json', b'{}', c.canonical({'model': 'audit-baselines-v1', 'baselines': [{}],
                                                          'quarantine': {'entries': [], 'dropped': 0}})):
            index.write_bytes(garbage)
            with self.assertRaises(StoreError) as caught:
                service.store.get(key, OWNER, now_ms)
            self.assertEqual(caught.exception.reason, 'store_invalid')
        index.write_bytes(saved)
        moved = self.governance / 'moved'
        service.store.root.rename(moved)
        service.store.root.symlink_to(moved)
        with self.assertRaises(StoreError) as caught:
            service.store.get(key, OWNER, now_ms)
        self.assertEqual(caught.exception.reason, 'store_invalid')
        self.assertEqual(self.governance.joinpath('moved', 'index.json').read_bytes(), saved)

    async def test_a_second_owner_is_refused_not_queued(self):
        service, key, entry, raw = await self.stored_record()
        now_ms = entry['created_at_ms'] + 1
        with service.store.transaction():
            with self.assertRaises(StoreError) as caught:
                BaselineStore(self.governance).get(key, OWNER, now_ms)
            self.assertEqual(caught.exception.reason, 'store_busy')
            error = await self.reason(service.compare(earlier_capture_id=key, later_capture_id=key))
            self.assertEqual((error.reason, error.retryable), ('capacity_busy', True))
            error = await self.reason(self.capture(service))
            self.assertEqual(error.reason, 'capacity_busy')
        self.assertEqual(len(self.entries(service)), 1)

    async def test_index_commit_fault_leaves_no_listed_partial_and_fails_closed(self):
        service, key, entry, raw = await self.stored_record()
        now_ms = entry['created_at_ms'] + 1

        def fail(stage):
            if stage == 'before_index':
                raise OSError('synthetic')
        service.store.fault_hook = fail
        with self.assertRaises(StoreError):
            self.put(service.store, 'b' * 32, raw, entry, now_ms)
        service.store.fault_hook = None
        self.assertEqual([item['capture_id'] for item in self.entries(service)], [key])
        service.store.get(key, OWNER, now_ms)
        self.assertFalse(self.record_path(service, 'b' * 32).exists())

        def after(stage):
            if stage == 'after_index':
                raise OSError('synthetic')
        service.store.fault_hook = after
        with self.assertRaises(StoreError):
            self.put(service.store, 'b' * 32, raw, entry, now_ms)
        service.store.fault_hook = None
        self.assertTrue(service.store.commit_unknown)
        with self.assertRaises(StoreError) as caught:
            service.store.get(key, OWNER, now_ms)
        self.assertEqual(caught.exception.reason, 'store_unavailable')


class ResumeTests(Harness):
    async def timed_out(self, data=None):
        service, client = self.service(data)
        with patch.object(c, 'COLLECTION_SECONDS', 10):
            page = await self.capture(service)
        self.assertTrue(all(r['configuration']['status'] == 'timed_out' for r in page['records']))
        return service, client, page

    async def test_resume_retries_only_timeouts_and_records_lineage(self):
        data = fixture(4)
        data['configs']['1'] = c.CaptureError('timeout')
        data['configs']['2'] = None
        service, client = self.service(data)
        first = await self.capture(service)
        statuses = {r['configuration_id']: r['configuration']['status'] for r in first['records']}
        self.assertEqual(statuses, {'0': 'readable', '1': 'unreadable', '2': 'unreadable', '3': 'readable'})
        self.assertEqual(first['record_outcomes'], {'1': 'timeout', '2': 'configuration_unavailable'})
        data['configs']['1'] = {'id': '1', 'mode': 'queued'}
        client.calls.clear()
        resumed = await self.capture(service, resume_capture_id=capture_id(first))
        self.assertEqual([call for call in client.calls if type(call) is tuple], [('config', '1')])
        rows = {r['configuration_id']: r for r in resumed['records']}
        self.assertEqual(rows['1']['configuration']['status'], 'readable')
        for kept in ('0', '2', '3'):
            self.assertEqual(rows[kept], next(r for r in first['records'] if r['configuration_id'] == kept))
        self.assertEqual(resumed['record_outcomes'], {'2': 'configuration_unavailable'})
        header = resumed['baseline_header']
        self.assertEqual(header['resume'], {'resumed_from': first['baseline_header']['baseline_id'],
                                            'resumed_count': 1})
        self.assertEqual(header['capture']['started_at'], first['baseline_header']['capture']['started_at'])
        self.assertEqual(resumed['snapshot']['resumed_from'], capture_id(first))
        entries = self.entries(service)
        self.assertEqual([e['resumed_from'] for e in entries], [None, capture_id(first)])
        # The earlier capture is still stored, unchanged.
        report, _ = service.store.get(capture_id(first), OWNER, service.now_ms())
        self.assertEqual(report['baseline']['records'][1]['configuration']['status'], 'unreadable')

    async def test_resume_of_scheduler_bound_timeouts_completes(self):
        service, client, first = await self.timed_out()
        self.assertEqual(set(first['record_outcomes'].values()), {'timeout'})
        resumed = await self.capture(service, resume_capture_id=capture_id(first))
        self.assertTrue(all(r['configuration']['status'] == 'readable' for r in resumed['records']))
        self.assertEqual(resumed['baseline_header']['resume']['resumed_count'], 3)
        self.assertEqual(resumed['record_outcomes'], {})
        self.assertEqual(resumed['baseline_header']['schema'], 'automation-audit-baseline-v2')

    async def test_resume_refusals_happen_before_a_store_write(self):
        def added(data):
            data['states'].append({'entity_id': 'automation.new', 'state': 'on', 'attributes': {'id': 'new'}})
            data['registry'].append({'entity_id': 'automation.new', 'unique_id': 'new',
                                     'platform': 'automation', 'disabled_by': None})
            data['configs']['new'] = {'id': 'new'}

        def renamed(data):
            data['states'][0]['entity_id'] = data['registry'][0]['entity_id'] = 'automation.renamed'

        def installation(data):
            data['devices'][0]['id'] = 'device2'

        def generation(_data):
            self.core.generation = 2

        for label, change in (('added', added), ('renamed', renamed), ('installation', installation),
                              ('generation', generation)):
            with self.subTest(label):
                self.core = Core()
                directory = tempfile.TemporaryDirectory()
                self.addCleanup(directory.cleanup)
                self.governance = Path(directory.name)
                service, client, first = await self.timed_out()
                change(client.data)
                error = await self.reason(self.capture(service, resume_capture_id=capture_id(first)))
                self.assertEqual(error.reason, 'resume_inventory_changed')
                self.assertEqual(len(self.entries(service)), 1)
                self.assertFalse(service.active)

    async def test_resume_not_needed_other_caller_and_damage_refuse_before_any_read(self):
        service, client = self.service()
        first = await self.capture(service)
        before = client.requests
        error = await self.reason(self.capture(service, resume_capture_id=capture_id(first)))
        self.assertEqual(error.reason, 'resume_not_needed')
        self.assertFalse(error.retryable)
        self.telemetry.caller_id = 'synthetic-other'
        self.assertEqual((await self.reason(self.capture(service, resume_capture_id=capture_id(first)))).reason,
                         'baseline_not_found')
        self.telemetry.caller_id = 'anonymous'
        self.assertEqual((await self.reason(self.capture(service, resume_capture_id=capture_id(first)))).reason,
                         'access_denied')
        self.telemetry.caller_id = OWNER
        self.record_path(service, capture_id(first)).write_bytes(b'{}')
        self.assertEqual((await self.reason(self.capture(service, resume_capture_id=capture_id(first)))).reason,
                         'baseline_damaged')
        self.assertEqual(client.requests, before)
        self.assertFalse(service.active)
        for bad in ({'resume_capture_id': 'A' * 32}, {'resume_capture_id': 'a' * 31},
                    {'resume_capture_id': 7}, {'resume_capture_id': 'a' * 32, 'cursor': '0' * 32 + ':0:' + '0' * 64}):
            with self.assertRaises(c.CaptureError):
                c.validate_arguments(bad)


class ValidationTests(Harness):
    async def baseline(self, timed_out=False):
        service, _ = self.service()
        if timed_out:
            with patch.object(c, 'COLLECTION_SECONDS', 10):
                page = await self.capture(service)
        else:
            page = await self.capture(service)
        report, _ = service.store.get(capture_id(page), OWNER, service.now_ms())
        return report['baseline']

    async def test_v2_validator_negatives(self):
        valid = await self.baseline(timed_out=True)
        baseline_from_value(valid)
        mutations = {
            'timed_out_digest': lambda b: b['records'][0]['configuration'].update(
                digest='sha256:' + '0' * 64, fingerprint_model=FINGERPRINT_MODEL),
            'v1_timed_out': lambda b: b.update(schema='automation-audit-baseline-v1'),
            'unknown_status': lambda b: b['records'][0]['configuration'].update(status='paused'),
            'resume_extra': lambda b: b.update(resume={'resumed_from': b['baseline_id'], 'resumed_count': 1, 'x': 1}),
            'resume_negative': lambda b: b.update(resume={'resumed_from': b['baseline_id'], 'resumed_count': -1}),
            'resume_missing': lambda b: b.pop('resume'),
            'schema': lambda b: b.update(schema='automation-audit-baseline-v3'),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                value = copy.deepcopy(valid)
                mutate(value)
                with self.assertRaises(BaselineValidationError):
                    baseline_from_value(value)
        with self.assertRaises(BaselineValidationError):
            baseline_from_value([valid])

    async def test_comparator_timed_out_is_unknown_with_reason(self):
        readable = baseline_from_value(await self.baseline())
        timed_out = baseline_from_value(await self.baseline(timed_out=True))
        for earlier, later in ((readable, timed_out), (timed_out, readable)):
            report = compare_baselines(earlier, later)
            self.assertEqual(report.counts['UNKNOWN'], 3)
            self.assertEqual(report.counts['UNCHANGED'], 0)
        rows = compare_recorded_fingerprints(timed_out, {'0': '0' * 64}, FINGERPRINT_MODEL)['records']
        self.assertIn('timed_out', rows[0]['reasons'])
        self.assertEqual(rows[0]['classification'], 'UNKNOWN')


class CompareTests(Harness):
    async def stored(self, data=None, service=None, client=None):
        if service is None:
            service, client = self.service(data)
        elif data is not None:
            client.data = data
        page = await self.capture(service)
        report, _ = service.store.get(capture_id(page), OWNER, service.now_ms())
        digests = {r['configuration_id']: r['configuration']['digest'][7:] for r in report['baseline']['records']
                   if r['configuration']['digest']}
        return service, client, capture_id(page), digests

    async def test_stored_captures_compare_without_any_home_assistant_read(self):
        service, client, first, _ = await self.stored()
        changed = fixture()
        changed['configs']['1']['mode'] = 'restart'
        _, _, second, _ = await self.stored(changed, service, client)
        before = client.requests
        result = await service.compare(earlier_capture_id=first, later_capture_id=second)
        self.assertEqual(client.requests, before)
        self.assertEqual(result['mode'], 'stored_captures')
        self.assertEqual((result['earlier_capture_id'], result['later_capture_id']), (first, second))
        self.assertEqual(result['counts'], {'ADDED': 0, 'REMOVED': 0, 'CHANGED': 1, 'UNCHANGED': 2,
                                            'UNKNOWN': 0, 'PREFIX_MATCH': 0, 'TOTAL': 3})
        self.assertEqual([r['configuration_id'] for r in result['records']], ['1', '0', '2'])
        self.assertEqual(result['record_order'], 'not_unchanged_or_prefix_match_first_then_configuration_id')
        self.assertEqual((result['screened_withheld_count'], result['details_truncated']), (0, False))
        # The unchanged comparator: one capture with itself, or in reverse
        # order, proves nothing.
        same = await service.compare(earlier_capture_id=first, later_capture_id=first)
        self.assertEqual((same['counts']['UNKNOWN'], same['counts']['UNCHANGED']), (3, 0))
        self.assertIn('same_baseline_id', json.dumps(same))
        reverse = await service.compare(earlier_capture_id=second, later_capture_id=first)
        self.assertEqual(reverse['counts']['UNKNOWN'], 3)
        self.assertIn('capture_intervals_not_ordered', json.dumps(reverse))

    async def test_recorded_fingerprints_full_prefix_changed_and_one_side_unknown(self):
        service, client, key, digests = await self.stored(fixture(4))
        wrong = ('f' if digests['2'][0] != 'f' else 'e') + digests['2'][1:16]
        rows = [{'configuration_id': '0', 'fingerprint': 'sha256:' + digests['0']},
                {'configuration_id': '1', 'fingerprint': 'sha256:' + digests['1'][:16]},
                {'configuration_id': '2', 'fingerprint': 'sha256:' + wrong},
                {'configuration_id': 'ghost', 'fingerprint': 'sha256:' + '0' * 16}]
        before = client.requests
        result = await service.compare(earlier_capture_id=key, recorded_fingerprints=rows,
                                       fingerprint_model=FINGERPRINT_MODEL)
        self.assertEqual(client.requests, before)
        self.assertEqual(result['mode'], 'recorded_fingerprints')
        self.assertIsNone(result['later_capture_id'])
        self.assertEqual(result['counts'], {'UNCHANGED': 1, 'PREFIX_MATCH': 1, 'CHANGED': 1, 'ADDED': 0,
                                            'REMOVED': 0, 'UNKNOWN': 2, 'TOTAL': 5})
        by_id = {r['configuration_id']: r for r in result['records']}
        self.assertEqual({k: (v['classification'], v['reasons']) for k, v in by_id.items()}, {
            '0': ('UNCHANGED', []), '1': ('PREFIX_MATCH', ['prefix_match:16']), '2': ('CHANGED', []),
            '3': ('UNKNOWN', ['absent_from_recorded_fingerprints']), 'ghost': ('UNKNOWN', ['absent_from_capture'])})
        self.assertEqual(by_id['1']['configuration_digest'], {'capture': 'sha256:' + digests['1'],
                                                             'recorded': digests['1'][:16]})
        self.assertIsNone(by_id['ghost']['entity_id'])
        self.assertEqual([r['configuration_id'] for r in result['records']], ['2', '3', 'ghost', '0', '1'])
        self.assertEqual(result['global']['recorded_side_inventory_authority'], 'not_recorded')
        self.assertIn('unchanged_counts_full_digest_matches_only_prefix_match_is_counted_separately',
                      result['limitations'])

    async def test_prefix_lengths_are_reported_and_only_64_is_unchanged(self):
        service, _, key, digests = await self.stored()
        for length, expected in ((16, 'PREFIX_MATCH'), (17, 'PREFIX_MATCH'), (63, 'PREFIX_MATCH'), (64, 'UNCHANGED')):
            with self.subTest(length):
                result = await service.compare(earlier_capture_id=key, fingerprint_model=FINGERPRINT_MODEL,
                    recorded_fingerprints=[{'configuration_id': i, 'fingerprint': 'sha256:' + d[:length]}
                                           for i, d in digests.items()])
                self.assertEqual(result['counts'][expected], 3)
                self.assertEqual(result['counts']['UNCHANGED' if expected == 'PREFIX_MATCH' else 'PREFIX_MATCH'], 0)
                reasons = [] if length == 64 else [f'prefix_match:{length}']
                self.assertTrue(all(r['reasons'] == reasons for r in result['records']))

    async def test_model_mismatch_and_empty_recorded_set_are_unknown(self):
        service, _, key, digests = await self.stored()
        rows = [{'configuration_id': i, 'fingerprint': 'sha256:' + d} for i, d in digests.items()]
        result = await service.compare(earlier_capture_id=key, recorded_fingerprints=rows,
                                       fingerprint_model='ha-automation-config-fingerprint-v1')
        self.assertEqual(result['counts']['UNKNOWN'], 3)
        self.assertFalse(result['global']['fingerprint_model_compatible'])
        self.assertTrue(all('fingerprint_model_mismatch' in r['reasons'] for r in result['records']))
        empty = await service.compare(earlier_capture_id=key, recorded_fingerprints=[],
                                      fingerprint_model=FINGERPRINT_MODEL)
        self.assertEqual((empty['counts']['UNKNOWN'], empty['counts']['REMOVED']), (3, 0))

    async def test_caller_binding_anonymous_and_damage(self):
        service, client, key, _ = await self.stored()
        self.telemetry.caller_id = 'synthetic-other'
        self.assertEqual((await self.reason(service.compare(earlier_capture_id=key, later_capture_id=key))).reason,
                         'baseline_not_found')
        self.telemetry.caller_id = 'anonymous'
        self.assertEqual((await self.reason(service.compare(earlier_capture_id=key, later_capture_id=key))).reason,
                         'access_denied')
        self.telemetry.caller_id = OWNER
        self.assertEqual((await self.reason(service.compare(earlier_capture_id=key, later_capture_id='0' * 32))).reason,
                         'baseline_not_found')
        self.record_path(service, key).write_bytes(b'{}')
        self.assertEqual((await self.reason(service.compare(earlier_capture_id=key, later_capture_id=key))).reason,
                         'baseline_damaged')

    def test_compare_argument_contract_is_closed(self):
        key, row = 'a' * 32, {'configuration_id': '1', 'fingerprint': 'sha256:' + '0' * 16}
        ok = {'earlier_capture_id': key, 'recorded_fingerprints': [row], 'fingerprint_model': FINGERPRINT_MODEL}
        self.assertEqual(c.validate_compare_arguments(ok), {'1': '0' * 16})
        self.assertIsNone(c.validate_compare_arguments({'earlier_capture_id': key, 'later_capture_id': 'b' * 32}))
        bad = [None, [], {}, {'later_capture_id': key}, {'earlier_capture_id': key},
               {'earlier_capture_id': 'A' * 32, 'later_capture_id': key},
               {'earlier_capture_id': key[:31], 'later_capture_id': key},
               {'earlier_capture_id': key, 'later_capture_id': 'z' * 32},
               {'earlier_capture_id': key, 'later_capture_id': 7},
               {'earlier_capture_id': key, 'later_capture_id': key, 'fingerprint_model': FINGERPRINT_MODEL},
               {**ok, 'later_capture_id': key}, {**ok, 'extra': 1},
               {**ok, 'fingerprint_model': ''}, {**ok, 'fingerprint_model': 'bad model'},
               {**ok, 'fingerprint_model': 'm' * 65}, {**ok, 'recorded_fingerprints': 'x'},
               {**ok, 'recorded_fingerprints': [row] * 2},
               {**ok, 'recorded_fingerprints': [{'configuration_id': str(i), 'fingerprint': row['fingerprint']}
                                                for i in range(1001)]}]
        for field, value in (('configuration_id', ''), ('configuration_id', 'a/b'), ('configuration_id', 1),
                             ('fingerprint', '0' * 16), ('fingerprint', 'sha256:' + '0' * 15),
                             ('fingerprint', 'sha256:' + '0' * 65), ('fingerprint', 'sha256:' + 'A' * 16),
                             ('fingerprint', 'sha1:' + '0' * 16), ('fingerprint', None)):
            bad.append({**ok, 'recorded_fingerprints': [{**row, field: value}]})
        bad.append({**ok, 'recorded_fingerprints': [{'configuration_id': '1'}]})
        bad.append({**ok, 'recorded_fingerprints': [{**row, 'note': 'x'}]})
        for arguments in bad:
            with self.subTest(arguments=str(arguments)[:80]):
                with self.assertRaises(c.CaptureError) as caught:
                    c.validate_compare_arguments(arguments)
                self.assertEqual(caught.exception.reason, 'invalid_arguments')

    async def test_bounded_output_keeps_attention_rows_first_and_reports_omissions(self):
        service, _, key, digests = await self.stored(fixture(60))
        rows = [{'configuration_id': i, 'fingerprint': 'sha256:' + d} for i, d in digests.items()]
        rows[-1] = {'configuration_id': rows[-1]['configuration_id'], 'fingerprint': 'sha256:' + 'f' * 16}
        service.page_limit = 6_000
        result = await service.compare(earlier_capture_id=key, recorded_fingerprints=rows,
                                       fingerprint_model=FINGERPRINT_MODEL)
        self.assertTrue(result['details_truncated'])
        self.assertEqual(result['omitted_detail_count'] + len(result['records']), 60)
        self.assertEqual(result['counts']['TOTAL'], 60)
        self.assertNotEqual(result['records'][0]['classification'], 'UNCHANGED')
        self.assertLessEqual(len(c.canonical(result)), 6_000)


class CredentialScreenTests(Harness):
    """The HAMCP-132 default-mode matrix through Projector.identifier and compare output."""

    def test_identifier_withholds_every_default_mode_case_and_shows_benign_names(self):
        projector = Projector((KNOWN,))
        for label, (value, _needle) in {**cases.withheld_cases(),
                                        **{k: (v, None) for k, v in cases.unverifiable_cases().items()}}.items():
            with self.subTest(label):
                self.assertIsNone(projector.identifier(value))
                self.assertIsNone(projector.identifier(value, entity=True))
        # The screen is what withholds these: the sanitizer alone shows them.
        for value in (KNOWN.upper(), KNOWN.swapcase()):
            sanitized = sanitize_untrusted_data(value, known_secrets=(KNOWN,), max_string=256)
            self.assertEqual(sanitized.value, value)
            self.assertIsNone(projector.identifier(value))
        self.assertIsNone(projector.identifier('x_' + KNOWN + '_y'))
        self.assertIsNone(projector.identifier('automation.' + KNOWN.lower(), entity=True))
        for value in ('token_refresh', 'password_reset_reminder', '1700000000000', 'Token.refresh:1'):
            self.assertEqual(projector.identifier(value), value)
        self.assertEqual(projector.identifier('automation.token_refresh', entity=True), 'automation.token_refresh')

    async def test_capture_drops_a_credential_shaped_identifier_and_stores_no_needle(self):
        data = fixture(2)
        secret_id = KNOWN.swapcase()
        data['states'].append({'entity_id': 'automation.leak', 'state': 'on', 'attributes': {'id': secret_id}})
        data['registry'].append({'entity_id': 'automation.leak', 'unique_id': secret_id,
                                 'platform': 'automation', 'disabled_by': None})
        data['configs'][secret_id] = {'id': secret_id}
        data['states'].append({'entity_id': 'automation.' + KNOWN.lower(), 'state': 'on', 'attributes': {'id': 'x'}})
        service, client = self.service(data)
        page = await self.capture(service)
        self.assertEqual([r['configuration_id'] for r in page['records']], ['0', '1'])
        self.assertNotIn(('config', secret_id), client.calls)
        stored = b''.join(p.read_bytes() for p in service.store.root.rglob('*') if p.is_file())
        for needle in (secret_id, KNOWN.lower(), KNOWN):
            self.assertNotIn(needle.encode(), stored)
            self.assertNotIn(needle, json.dumps(page))

    async def test_compare_output_withholds_recorded_credential_shaped_ids(self):
        service, _ = self.service()
        key = capture_id(await self.capture(service))
        shaped = [v for v, _ in cases.withheld_cases().values() if c.ID.fullmatch(v)]
        self.assertGreaterEqual(len(shaped), 2)
        rows = [{'configuration_id': v, 'fingerprint': 'sha256:' + '0' * 16} for v in shaped]
        result = await service.compare(earlier_capture_id=key, recorded_fingerprints=rows,
                                       fingerprint_model=FINGERPRINT_MODEL)
        rendered = json.dumps(result)
        for value in shaped:
            self.assertNotIn(value, rendered)
        self.assertEqual(result['screened_withheld_count'], len(shaped))
        self.assertEqual(result['counts']['UNKNOWN'], 3 + len(shaped))
        withheld = [r for r in result['records'] if untrusted_text.withheld(r['configuration_id'])]
        self.assertEqual(len(withheld), len(shaped))
        # Not shaped like an identifier: refused at the argument contract, never echoed.
        for value, _ in list(cases.withheld_cases().values())[:5]:
            if not c.ID.fullmatch(value):
                with self.assertRaises(c.CaptureError):
                    c.validate_compare_arguments({'earlier_capture_id': key, 'fingerprint_model': FINGERPRINT_MODEL,
                        'recorded_fingerprints': [{'configuration_id': value, 'fingerprint': 'sha256:' + '0' * 16}]})


FIXTURES = ROOT / 'tests' / 'fixtures'


class BetaThirtySevenV1FixtureTests(Harness):
    """The v1 baseline written by the shipped beta.37 capture code still loads and compares."""

    def setUp(self):
        super().setUp()
        self.provenance = json.loads((FIXTURES / 'hamcp228_beta37_v1_baseline_provenance.json').read_text())
        self.path = FIXTURES / 'hamcp228_beta37_v1_baseline.json'
        self.raw = self.path.read_bytes()

    def test_provenance_binds_the_exact_bytes_generator_and_source(self):
        provenance = self.provenance
        self.assertEqual(provenance['source']['commit'], '06a28134f219244f258ab26307c1dfcb9e5d18cb')
        self.assertEqual(provenance['source']['version'], '2.4.1-beta.37')
        self.assertEqual(provenance['fixture']['sha256'], hashlib.sha256(self.raw).hexdigest())
        generator = ROOT / provenance['generator']
        self.assertEqual(provenance['generator_sha256'], hashlib.sha256(generator.read_bytes()).hexdigest())
        data = (FIXTURES / 'hamcp228_beta37_v1_baseline_input.json').read_bytes()
        self.assertEqual(provenance['input']['sha256'], hashlib.sha256(data).hexdigest())
        baseline = json.loads(self.raw)
        # Unedited canonical export: the bytes are the shipped artifact digest.
        self.assertEqual(c.canonical(baseline), self.raw)
        self.assertEqual(c.digest(baseline), provenance['fixture']['artifact_sha256'])
        self.assertEqual(baseline['schema'], 'automation-audit-baseline-v1')
        self.assertNotIn('resume', baseline)

    async def test_v1_loads_and_compares_with_a_current_v2_capture(self):
        from ha_mcp_engineering.audit_baseline import load_baseline
        earlier = load_baseline(self.path)
        self.assertEqual(baseline_from_value(json.loads(self.raw)).baseline_id, earlier.baseline_id)
        data = json.loads((FIXTURES / 'hamcp228_beta37_v1_baseline_input.json').read_text())
        service, _ = self.service(data)
        page = await self.capture(service)
        report, _ = service.store.get(capture_id(page), OWNER, service.now_ms())
        self.assertEqual(report['baseline']['schema'], 'automation-audit-baseline-v2')
        later = baseline_from_value(report['baseline'])
        result = compare_baselines(earlier, later)
        self.assertEqual(result.counts, {'ADDED': 0, 'REMOVED': 0, 'CHANGED': 0, 'UNCHANGED': 2,
                                         'UNKNOWN': 1, 'TOTAL': 3})
        digests = {r['configuration_id']: r['configuration']['digest'] for r in json.loads(self.raw)['records']}
        fingerprints = {k: v[7:23] for k, v in digests.items() if v}
        recorded = compare_recorded_fingerprints(earlier, fingerprints, FINGERPRINT_MODEL)
        self.assertEqual((recorded['counts']['PREFIX_MATCH'], recorded['counts']['UNCHANGED'],
                          recorded['counts']['UNKNOWN']), (2, 0, 1))


class PublicToolTests(Harness):
    async def serve(self, service, arguments):
        import httpx
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.audit_baseline.capture_runtime import AUTOMATION_BASELINE_CAPTURE
        from ha_mcp_engineering.configuration import Settings
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from ha_mcp_engineering.tools import get_registered_server, registered_tools
        end_request(self.token)
        self.token = None

        async def app(scope, receive, send):
            rpc = json.loads((await receive())['body'])
            from ha_mcp_engineering.request_context import current_telemetry
            telemetry = current_telemetry()
            telemetry.caller_id = OWNER
            rendered = await registered_tools(get_registered_server())['compare_automation_baselines'].run(
                rpc['params']['arguments'])
            body = {'jsonrpc': '2.0', 'id': rpc['id'],
                    'result': {'content': [{'type': 'text', 'text': rendered}], 'isError': False}}
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-type', b'application/json')]})
            await send({'type': 'http.response.body', 'body': json.dumps(body).encode()})

        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / 'audit.jsonl'
            settings = Settings(ha_url='http://synthetic.invalid', ha_token='SYNTHETIC_HA_TOKEN',
                                access_secret='SYNTHETIC_ACCESS_TOKEN', port=8100, audit_path=str(audit),
                                rate_limit_per_minute=1000, rate_limit_burst=1000,
                                destructive_services=frozenset())
            core = SimpleNamespace(acquire=Mock(return_value=object()), consume=Mock(return_value=()),
                                   revalidate=Mock(return_value=True), finish=Mock(return_value=True),
                                   release=Mock(return_value=True))
            gateway = AuthenticatedMcpGateway(app, settings, AuditLogger(str(audit), settings.access_secret),
                                              core_runtime=core)
            with patch.object(AUTOMATION_BASELINE_CAPTURE, 'service', service):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                             base_url='http://127.0.0.1:8100') as http:
                    response = await http.post('/SYNTHETIC_ACCESS_TOKEN/mcp', json={
                        'jsonrpc': '2.0', 'id': 'synthetic-request', 'method': 'tools/call',
                        'params': {'name': 'compare_automation_baselines', 'arguments': arguments}})
            return response.text, audit.read_text()

    def tearDown(self):
        if self.token is not None:
            end_request(self.token)

    async def test_descriptor_is_read_only_closed_and_local(self):
        from ha_mcp_engineering.tools import get_registered_server, registered_tools
        from ha_mcp_engineering.providers.routing import routing_for_tool
        tool = registered_tools(get_registered_server())['compare_automation_baselines']
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertTrue(tool.annotations.idempotentHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertFalse(tool.annotations.openWorldHint)
        self.assertFalse(tool.parameters['additionalProperties'])
        self.assertEqual(routing_for_tool('compare_automation_baselines').fallback_providers, ())
        self.assertEqual(set(tool.parameters['properties']),
                         {'earlier_capture_id', 'later_capture_id', 'recorded_fingerprints', 'fingerprint_model'})
        for arguments in ({'earlier_capture_id': SECRET}, {'earlier_capture_id': 'a' * 32, 'PRIVATE_KEY': SECRET},
                          {'earlier_capture_id': 'a' * 32, 'recorded_fingerprints': [{'configuration_id': SECRET,
                                                                                      'fingerprint': SECRET}],
                           'fingerprint_model': FINGERPRINT_MODEL}, None):
            response = await tool.run(arguments)
            self.assertFalse(json.loads(response)['success'])
            self.assertNotIn(SECRET, response)
            self.assertNotIn('PRIVATE_KEY', response)

    async def test_gateway_audit_carries_counts_and_presence_only(self):
        service, _ = self.service()
        key = capture_id(await self.capture(service))
        report, _ = service.store.get(key, OWNER, service.now_ms())
        digest = report['baseline']['records'][0]['configuration']['digest']
        rows = [{'configuration_id': '0', 'fingerprint': digest[:23]},
                {'configuration_id': 'ghost_id', 'fingerprint': 'sha256:' + '1' * 16}]
        response, audit = await self.serve(service, {'earlier_capture_id': key, 'recorded_fingerprints': rows,
                                                     'fingerprint_model': FINGERPRINT_MODEL})
        data = json.loads(json.loads(response)['result']['content'][0]['text'])
        self.assertTrue(data['success'], data)
        self.assertEqual(data['data']['counts']['PREFIX_MATCH'], 1)
        self.assertEqual(data['metadata']['data_providers'], [])
        record = next(json.loads(r) for r in audit.splitlines()
                      if json.loads(r).get('tool_name') == 'compare_automation_baselines')
        self.assertEqual(record['parameters'], {
            'model': 'automation-baseline-compare-v1', 'later_capture_present': False,
            'recorded_fingerprint_count': 2, 'fingerprint_model_present': True,
            'provider': 'engineering', 'fallback': 'none'})
        self.assertEqual(record['resource_ids'], {})
        for value in (key, 'ghost_id', digest[7:23], '1' * 16):
            self.assertNotIn(value, audit)

    async def test_gateway_refuses_invalid_arguments_without_echo_or_dispatch(self):
        import httpx
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.configuration import Settings
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / 'audit.jsonl'
            settings = Settings(ha_url='http://synthetic.invalid', ha_token='SYNTHETIC_HA_TOKEN',
                                access_secret='SYNTHETIC_ACCESS_TOKEN', port=8100, audit_path=str(audit),
                                rate_limit_per_minute=1000, rate_limit_burst=1000,
                                destructive_services=frozenset())
            app = Mock(side_effect=AssertionError('SDK must not run'))
            core = SimpleNamespace(acquire=Mock())
            gateway = AuthenticatedMcpGateway(app, settings, AuditLogger(str(audit), settings.access_secret),
                                              core_runtime=core)
            for arguments in ({'earlier_capture_id': SECRET, 'PRIVATE_KEY': SECRET},
                              {'earlier_capture_id': 'a' * 32, 'fingerprint_model': FINGERPRINT_MODEL,
                               'recorded_fingerprints': [{'configuration_id': SECRET, 'fingerprint': SECRET}]}):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                             base_url='http://127.0.0.1:8100') as http:
                    response = await http.post('/SYNTHETIC_ACCESS_TOKEN/mcp', json={
                        'jsonrpc': '2.0', 'id': 'synthetic-request', 'method': 'tools/call',
                        'params': {'name': 'compare_automation_baselines', 'arguments': arguments}})
                self.assertIn('invalid_request', response.text)
                self.assertNotIn(SECRET, response.text + audit.read_text())
                self.assertNotIn('PRIVATE_KEY', audit.read_text())
            core.acquire.assert_not_called()
            app.assert_not_called()


if __name__ == '__main__':
    unittest.main()
