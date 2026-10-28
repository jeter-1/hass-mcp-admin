"""HAMCP-235: baseline compare and resume usable on the owner's installation.

Live-shaped first: the beta.40 findings (HAOS, Core 2026.10.3, 115
automations, two captures seconds apart whose final fence failed) must
compare UNCHANGED with one comparison-level caveat, and a resume minutes
later with the same automations must succeed. Synthetic only: the fake
client and Core come from ``test_automation_baseline_capture``.
"""
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from functools import partial
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]

from test_automation_baseline_capture import Client, Core, fixture  # noqa: E402
from test_hamcp228_baseline_store import OWNER, Harness, capture_id  # noqa: E402
from ha_mcp_engineering.audit_baseline import capture_contracts as c  # noqa: E402
from ha_mcp_engineering.audit_baseline.capture_provider import BaselineCaptureProvider  # noqa: E402
from ha_mcp_engineering.audit_baseline.comparison import CAVEAT_STATEMENTS  # noqa: E402
from ha_mcp_engineering.audit_baseline.models import FINGERPRINT_MODEL  # noqa: E402

LIVE_COUNT = 115
LIVE_CORE = '2026.10.3'
BOTH = ['earlier', 'later']


def caveat(code, sides):
    return {'code': code, 'statement': CAVEAT_STATEMENTS[code], 'sides': sides}


class LiveHarness(Harness):
    """The owner's installation shape, with a final fence that can fail."""

    def setUp(self):
        super().setUp()
        self.core.current_observation.version = LIVE_CORE

    def live(self, count=LIVE_COUNT):
        service, client = self.service(fixture(count))
        client.principal_reads = 0
        client.fail_final = True

        async def hook(kind):
            # Each capture reads the principal once per fence; the second
            # (final-fence) read times out, as the shared deadline did live.
            if kind == 'principal':
                client.principal_reads += 1
                if client.fail_final and client.principal_reads % 2 == 0:
                    raise c.CaptureError('timeout')
        client.hook = hook
        return service, client

    def fresh_store(self):
        self.core = Core()
        self.core.current_observation.version = LIVE_CORE
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.governance = Path(directory.name)

    async def compare_all(self, service, **arguments):
        """Every page of a comparison; each page must carry the caveats once."""

        pages = [await service.compare(**arguments)]
        while pages[-1]['pagination']['next_cursor']:
            pages.append(await service.compare(cursor=pages[-1]['pagination']['next_cursor'], **arguments))
        codes = [item['code'] for item in pages[0]['caveats']]
        self.assertEqual(len(codes), len(set(codes)), 'each caveat is stated once')
        for page in pages:
            self.assertEqual(page['caveats'], pages[0]['caveats'])
            reasons = json.dumps([row['reasons'] for row in page['records']])
            for code in (*CAVEAT_STATEMENTS, 'installation_identity_unestablished', 'authority_continuity'):
                self.assertNotIn(code, reasons, 'a comparison-level caveat is never a row reason')
        return pages[0], {row['configuration_id']: row for page in pages for row in page['records']}

    def assertUnestablished(self, page):
        header, diagnostics = page['baseline_header'], page['diagnostics']
        self.assertEqual(header['installation']['status'], 'unestablished')
        self.assertIsNone(header['installation']['installation_id'])
        self.assertEqual(header['consistency'], {
            'inventory_drift': 'unknown', 'authority_drift': 'unknown',
            'limitations': ['non_atomic_changes_and_reversions_may_be_unobserved']})
        self.assertEqual((diagnostics['final_fence'], diagnostics['final_fence_reason']), ('unavailable', 'timeout'))
        self.assertRegex(diagnostics['first_fence_installation_id'], r'\Asha256:[0-9a-f]{64}\Z')


class LiveShapedCompareTests(LiveHarness):
    async def test_live_pair_unestablished_identical_digests_is_unchanged_with_one_caveat(self):
        service, client = self.live()
        first = await self.capture(service)
        self.now += 4
        second = await self.capture(service)
        for page in (first, second):
            self.assertUnestablished(page)
            self.assertEqual(page['baseline_header']['inventory']['declared_count'], LIVE_COUNT)
        before = client.requests
        result, rows = await self.compare_all(service, earlier_capture_id=capture_id(first),
                                              later_capture_id=capture_id(second))
        self.assertEqual(client.requests, before, 'compare reads no Home Assistant data')
        self.assertEqual(result['counts'], {'ADDED': 0, 'REMOVED': 0, 'CHANGED': 0, 'UNCHANGED': LIVE_COUNT,
                                            'UNKNOWN': 0, 'PREFIX_MATCH': 0, 'TOTAL': LIVE_COUNT})
        self.assertEqual(len(rows), LIVE_COUNT)
        self.assertTrue(all(row['classification'] == 'UNCHANGED' and row['reasons'] == [] for row in rows.values()))
        self.assertEqual(result['caveats'], [caveat('installation_identity_unverified', BOTH),
                                             caveat('authority_continuity_unestablished', BOTH),
                                             caveat('non_atomic_capture', BOTH)])
        self.assertFalse(result['global']['installation_comparable'])
        self.assertFalse(result['global']['authority_continuity_comparable'])

    async def test_live_pair_with_a_real_change_is_changed_with_the_caveat(self):
        service, client = self.live()
        first = await self.capture(service)
        client.data['configs']['7']['mode'] = 'restart'
        self.now += 4
        second = await self.capture(service)
        result, rows = await self.compare_all(service, earlier_capture_id=capture_id(first),
                                              later_capture_id=capture_id(second))
        self.assertEqual((result['counts']['CHANGED'], result['counts']['UNCHANGED'], result['counts']['UNKNOWN']),
                         (1, LIVE_COUNT - 1, 0))
        self.assertEqual((rows['7']['classification'], rows['7']['reasons']), ('CHANGED', []))
        self.assertEqual(result['records'][0]['configuration_id'], '7', 'attention rows first')
        self.assertEqual([item['code'] for item in result['caveats']], [
            'installation_identity_unverified', 'authority_continuity_unestablished', 'non_atomic_capture'])

    async def test_live_pair_timed_out_rows_are_unknown_with_their_reason(self):
        service, client = self.live()
        first = await self.capture(service)
        for key in ('3', '9'):
            client.data['configs'][key] = c.CaptureError('timeout')
        client.data['configs']['12'] = None  # unavailable: unknown, but not a timeout
        self.now += 4
        second = await self.capture(service)
        self.assertEqual(second['diagnostics']['configuration_incomplete_count'], 3)
        result, rows = await self.compare_all(service, earlier_capture_id=capture_id(first),
                                              later_capture_id=capture_id(second))
        self.assertEqual((result['counts']['UNKNOWN'], result['counts']['UNCHANGED']), (3, LIVE_COUNT - 3))
        for key in ('3', '9'):
            self.assertEqual(rows[key]['classification'], 'UNKNOWN')
            self.assertIn('timed_out', rows[key]['reasons'])
            self.assertIn('later_configuration_digest_missing', rows[key]['reasons'])
        self.assertEqual(rows['12']['classification'], 'UNKNOWN')
        self.assertNotIn('timed_out', rows['12']['reasons'])
        self.assertIn('later_configuration_digest_missing', rows['12']['reasons'])
        for row in rows.values():
            for reason in ('installation_identity_unestablished', 'earlier_authority_continuity_unestablished',
                           'later_authority_continuity_unestablished'):
                self.assertNotIn(reason, row['reasons'], 'a global caveat is never a row reason')

    async def test_earlier_side_timeout_is_unknown_with_its_reason(self):
        service, client = self.live(count=4)
        original = client.data['configs']['2']
        client.data['configs']['2'] = c.CaptureError('timeout')
        first = await self.capture(service)
        client.data['configs']['2'] = original
        self.now += 4
        second = await self.capture(service)
        result, rows = await self.compare_all(service, earlier_capture_id=capture_id(first),
                                              later_capture_id=capture_id(second))
        self.assertEqual((result['counts']['UNKNOWN'], result['counts']['UNCHANGED']), (1, 3))
        self.assertEqual(rows['2']['classification'], 'UNKNOWN')
        self.assertLessEqual({'timed_out', 'earlier_configuration_digest_missing'}, set(rows['2']['reasons']))

    async def test_scheduler_bound_timeouts_are_unknown_with_their_reason(self):
        service, _ = self.live(count=3)
        first = await self.capture(service)
        with patch.object(c, 'COLLECTION_SECONDS', c.FINAL_FENCE_RESERVE_MIN):
            second = await self.capture(service)
        result, rows = await self.compare_all(service, earlier_capture_id=capture_id(first),
                                              later_capture_id=capture_id(second))
        self.assertEqual(result['counts']['UNKNOWN'], 3)
        self.assertTrue(all('timed_out' in row['reasons'] for row in rows.values()))

    async def test_recorded_fingerprints_against_an_unestablished_capture(self):
        service, client = self.live(count=4)
        client.data['configs']['3'] = c.CaptureError('timeout')
        page = await self.capture(service)
        report, _ = service.store.get(capture_id(page), OWNER, service.now_ms())
        digests = {r['configuration_id']: r['configuration']['digest'] for r in report['baseline']['records']}
        rows = [{'configuration_id': key, 'fingerprint': digests[key] if key != '2' else digests[key][:23]}
                for key in ('0', '1', '2', '3')]
        rows[3]['fingerprint'] = 'sha256:' + '0' * 64
        result, by_id = await self.compare_all(service, earlier_capture_id=capture_id(page),
                                               recorded_fingerprints=rows, fingerprint_model=FINGERPRINT_MODEL)
        self.assertEqual({k: (v['classification'], v['reasons']) for k, v in by_id.items() if k != '3'}, {
            '0': ('UNCHANGED', []), '1': ('UNCHANGED', []), '2': ('PREFIX_MATCH', ['prefix_match:16'])})
        self.assertEqual(by_id['3']['classification'], 'UNKNOWN')
        self.assertLessEqual({'capture_configuration_digest_missing', 'timed_out'}, set(by_id['3']['reasons']))
        self.assertEqual(result['caveats'], [
            caveat('installation_identity_unverified', ['capture', 'recorded_fingerprints']),
            caveat('authority_continuity_unestablished', ['capture']),
            caveat('non_atomic_capture', ['capture'])])

    async def test_fingerprint_model_mismatch_is_unknown_with_its_reason(self):
        service, client = self.live(count=2)
        page = await self.capture(service)
        report, _ = service.store.get(capture_id(page), OWNER, service.now_ms())
        rows = [{'configuration_id': r['configuration_id'], 'fingerprint': r['configuration']['digest']}
                for r in report['baseline']['records']]
        result, by_id = await self.compare_all(service, earlier_capture_id=capture_id(page),
                                               recorded_fingerprints=rows, fingerprint_model='other-model-v1')
        self.assertEqual(result['counts']['UNKNOWN'], 2)
        self.assertTrue(all(row['reasons'] for row in by_id.values()))
        self.assertTrue(all('model' in ' '.join(row['reasons']) for row in by_id.values()), by_id)

    async def test_established_pair_has_only_the_non_atomic_caveat(self):
        service, client = self.live(count=3)
        client.fail_final = False
        first = await self.capture(service)
        second = await self.capture(service)
        self.assertEqual(first['diagnostics']['final_fence'], 'complete')
        result, rows = await self.compare_all(service, earlier_capture_id=capture_id(first),
                                              later_capture_id=capture_id(second))
        self.assertEqual(result['caveats'], [caveat('non_atomic_capture', BOTH)])
        self.assertEqual(result['counts']['UNCHANGED'], 3)

    async def test_two_established_different_installations_stay_unknown_per_row(self):
        service, client = self.live(count=3)
        client.fail_final = False
        first = await self.capture(service)
        client.data['devices'][0]['id'] = 'device2'
        second = await self.capture(service)
        result, rows = await self.compare_all(service, earlier_capture_id=capture_id(first),
                                              later_capture_id=capture_id(second))
        self.assertEqual(result['counts']['UNKNOWN'], 3)
        self.assertTrue(all(row['reasons'] == ['installation_identity_mismatch'] for row in rows.values()))
        self.assertNotIn('installation_identity_unverified', [item['code'] for item in result['caveats']])


class LiveShapedResumeTests(LiveHarness):
    async def live_base(self, timeouts=('3', '9')):
        service, client = self.live()
        for key in timeouts:
            client.data['configs'][key] = c.CaptureError('timeout')
        base = await self.capture(service)
        self.assertUnestablished(base)
        report, _ = service.store.get(capture_id(base), OWNER, service.now_ms())
        self.assertEqual(report['diagnostics']['record_outcomes'], {key: 'timeout' for key in timeouts})
        for key in timeouts:
            client.data['configs'][key] = {'id': key, 'mode': 'single'}
        return service, client, base

    async def test_resume_minutes_later_with_unchanged_automations_succeeds(self):
        service, client, base = await self.live_base()
        # Two minutes later: a Core reconnect advanced the authority
        # generation, automations were switched on and off, and an
        # automation without a configuration id appeared. None is identity.
        self.now += 120
        self.core.generation += 1
        client.data['states'][0]['state'] = 'on'
        client.data['states'][5]['state'] = 'off'
        client.data['states'].append({'entity_id': 'automation.yaml_only', 'state': 'on', 'attributes': {}})
        client.fail_final = False
        client.calls.clear()
        resumed = await self.capture(service, resume_capture_id=capture_id(base))
        self.assertEqual(sorted(call for call in client.calls if type(call) is tuple),
                         [('config', '3'), ('config', '9')], 'only the timed-out reads are retried')
        header = resumed['baseline_header']
        self.assertEqual(header['resume'], {'resumed_from': base['baseline_header']['baseline_id'],
                                            'resumed_count': 2})
        report, _ = service.store.get(capture_id(resumed), OWNER, service.now_ms())
        self.assertEqual(report['diagnostics']['record_outcomes'], {})
        self.assertEqual(resumed['diagnostics']['final_fence'], 'complete')
        # The base's records are bracketed by its first-fence anchor and this
        # capture's verified fences.
        self.assertEqual(header['installation']['status'], 'established')
        self.assertEqual(header['installation']['installation_id'],
                         base['diagnostics']['first_fence_installation_id'])
        self.assertEqual(header['consistency']['authority_drift'], 'detected')
        self.assertEqual(len(self.entries(service)), 2)
        # A fresh capture of the same automations compares UNCHANGED.
        self.now += 4
        fresh = await self.capture(service)
        result, rows = await self.compare_all(service, earlier_capture_id=capture_id(resumed),
                                              later_capture_id=capture_id(fresh))
        self.assertEqual((result['counts']['UNCHANGED'], result['counts']['UNKNOWN']), (LIVE_COUNT, 0))
        self.assertEqual(result['caveats'], [caveat('authority_continuity_unestablished', ['earlier']),
                                             caveat('non_atomic_capture', BOTH)])

    async def test_each_volatile_input_alone_resumes(self):
        def generation(_data):
            self.core.generation += 1

        def toggled(data):
            for row in data['states']:
                row['state'] = 'off' if row['state'] == 'on' else 'on'

        def unmapped(data):
            data['states'].append({'entity_id': 'automation.zz', 'state': 'on', 'attributes': {}})

        def registry_only(data):
            data['registry'].append({'entity_id': 'automation.registry_only', 'unique_id': 'r',
                                     'platform': 'automation', 'disabled_by': None})

        for label, change in (('generation', generation), ('toggled', toggled), ('unmapped', unmapped),
                              ('registry_only', registry_only)):
            with self.subTest(label):
                self.fresh_store()
                service, client, base = await self.live_base(timeouts=('3',))
                change(client.data)
                self.now += 120
                resumed = await self.capture(service, resume_capture_id=capture_id(base))
                self.assertEqual(resumed['baseline_header']['resume']['resumed_count'], 1)
                self.assertEqual(resumed['record_outcomes'], {})

    async def test_each_inventory_change_category_is_refused_precisely(self):
        def added(data):
            data['states'].append({'entity_id': 'automation.new', 'state': 'on', 'attributes': {'id': 'new'}})
            data['registry'].append({'entity_id': 'automation.new', 'unique_id': 'new',
                                     'platform': 'automation', 'disabled_by': None})
            data['configs']['new'] = {'id': 'new'}

        def removed(data):
            data['states'] = [row for row in data['states'] if row['entity_id'] != 'automation.a50']

        def mapping(data):
            data['states'][1]['entity_id'] = data['registry'][1]['entity_id'] = 'automation.renamed'

        def installation(data):
            data['devices'][0]['id'] = 'device2'

        def replaced(data):
            added(data)
            removed(data)

        def core(_data):
            self.core.current_observation.version = '2026.11.0'

        for label, change, reason, expected in (
                ('added', added, 'resume_inventory_changed', ['automation_added']),
                ('removed', removed, 'resume_inventory_changed', ['automation_removed']),
                ('mapping', mapping, 'resume_inventory_changed', ['mapping_changed']),
                ('installation', installation, 'resume_inventory_changed', ['installation_changed']),
                ('replaced', replaced, 'resume_inventory_changed', ['automation_added', 'automation_removed']),
                ('core', core, 'resume_core_changed', ['core_version_changed'])):
            with self.subTest(label):
                self.fresh_store()
                service, client, base = await self.live_base(timeouts=('3',))
                change(client.data)
                self.now += 120
                client.calls.clear()
                error = await self.reason(self.capture(service, resume_capture_id=capture_id(base)))
                self.assertEqual(error.details, {'reason': reason, 'resume_changes': expected})
                self.assertFalse(error.retryable)
                self.assertEqual([call for call in client.calls if type(call) is tuple], [],
                                 'no configuration read before a refusal')
                self.assertEqual(len(self.entries(service)), 1, 'nothing persisted before a refusal')
                self.assertFalse(service.active)

    async def test_an_anchorless_base_is_refused_before_any_read_or_write(self):
        # Owner decision D4 (Josh, 2026-10-28): a capture that recorded no
        # first-fence anchor is never resumed, on any installation.
        for label, installation in (('same', None), ('different', 'device2')):
            with self.subTest(label):
                self.fresh_store()
                service, client, base = await self.live_base(timeouts=('3',))
                key = capture_id(base)
                report, entry = service.store.get(key, OWNER, service.now_ms())
                report['diagnostics'].pop('first_fence_installation_id')
                if installation:
                    client.data['devices'][0]['id'] = installation
                client.fail_final = False
                client.calls.clear()
                with patch.object(service.store, 'get', return_value=(report, entry)):
                    error = await self.reason(self.capture(service, resume_capture_id=key))
                self.assertEqual(error.details, {'reason': 'resume_inventory_changed',
                                                 'resume_changes': ['installation_anchor_missing']})
                self.assertFalse(error.retryable)
                self.assertEqual(client.calls, [], 'nothing is read before the refusal')
                self.assertEqual(len(self.entries(service)), 1, 'nothing is persisted')
                self.assertFalse(service.active)

    async def test_inherited_rows_keep_their_own_provenance(self):
        # Behind the service gate: a base without an anchor that reached the
        # provider keeps no anchor, so no chain of resumes establishes it,
        # and the current fence's anchor is never recorded for its rows.
        service, client, base = await self.live_base(timeouts=('3', '9'))
        report, _ = service.store.get(capture_id(base), OWNER, service.now_ms())
        report['diagnostics'].pop('first_fence_installation_id')
        client.fail_final = False
        client.data['configs']['9'] = c.CaptureError('timeout')
        first = await service.provider.collect(report)
        self.assertEqual(first['baseline']['installation'], {**first['baseline']['installation'],
                                                            'status': 'unestablished', 'installation_id': None})
        self.assertIsNone(first['diagnostics']['first_fence_installation_id'])
        self.assertEqual(first['diagnostics']['record_outcomes'], {'9': 'timeout'})
        client.data['configs']['9'] = {'id': '9', 'mode': 'single'}
        second = await service.provider.collect(first)
        self.assertEqual(second['baseline']['installation']['status'], 'unestablished')
        self.assertIsNone(second['diagnostics']['first_fence_installation_id'])
        self.assertEqual(second['diagnostics']['record_outcomes'], {})
        # An anchored base keeps its own anchor through the same chain.
        anchored, _ = service.store.get(capture_id(base), OWNER, service.now_ms())
        resumed = await service.provider.collect(anchored)
        self.assertEqual(resumed['diagnostics']['first_fence_installation_id'],
                         anchored['diagnostics']['first_fence_installation_id'])
        self.assertEqual(resumed['baseline']['installation']['installation_id'],
                         anchored['diagnostics']['first_fence_installation_id'])


class FinalFenceTests(LiveHarness):
    async def test_volatile_state_between_fences_is_not_inventory_drift(self):
        service, client = self.live(count=3)
        client.fail_final = False

        async def hook(kind):
            if kind == 'config':
                client.data['states'][1]['state'] = 'off'
        client.hook = hook
        page = await self.capture(service)
        self.assertEqual(page['baseline_header']['consistency']['inventory_drift'],
                         'none_observed_at_capture_fences')

    async def test_a_mapping_change_between_fences_is_detected(self):
        service, client = self.live(count=3)

        async def hook(kind):
            if kind == 'config':
                client.data['states'][1]['entity_id'] = 'automation.moved'
        client.hook = hook
        page = await self.capture(service)
        self.assertEqual(page['baseline_header']['consistency']['inventory_drift'], 'detected')

    async def test_an_inventory_read_failure_keeps_the_verified_identity(self):
        service, client = self.live(count=3)
        reads = []

        async def hook(kind):
            reads.append(kind)
            if kind == 'states' and reads.count('states') == 2:
                raise c.CaptureError('timeout')
        client.hook = hook
        page = await self.capture(service)
        header, diagnostics = page['baseline_header'], page['diagnostics']
        self.assertEqual((diagnostics['final_fence'], diagnostics['final_fence_reason']), ('identity_only', 'timeout'))
        self.assertEqual(header['installation']['status'], 'established')
        self.assertEqual(header['consistency']['inventory_drift'], 'unknown')
        self.assertEqual(header['consistency']['authority_drift'], 'none_observed_at_capture_fences')

    async def test_identity_drift_still_refuses_and_stores_nothing(self):
        service, client = self.live(count=3)
        client.fail_final = False

        async def hook(kind):
            if kind == 'config':
                client.data['devices'][0]['id'] = 'device2'
        client.hook = hook
        error = await self.reason(self.capture(service))
        self.assertEqual(error.reason, 'identity_drift')
        self.assertEqual(self.entries(service), [])



class DeadlineClient(Client):
    """The fake client under ``BaselinePeer``'s bounds, with per-read latency.

    As ``BaselinePeer.remaining()``: one collection bound from the start of the
    capture, each read at most ``READ_SECONDS``. ``delay(kind, fence)`` gives a
    read's latency; ``fence`` is 0 for the first fence, 1 for the final one.
    """

    def __init__(self, data, delay):
        super().__init__(data)
        self.delay, self.principal_reads = delay, 0
        self.started = None
        self.timeline = []

    @asynccontextmanager
    async def capture(self, version, authorize):
        self.started = time.monotonic()
        async with super().capture(version, authorize) as peer:
            yield peer

    def remaining(self):
        seconds = c.COLLECTION_SECONDS - (time.monotonic() - self.started)
        if seconds <= 0:
            raise c.CaptureError('timeout')
        return min(c.READ_SECONDS, seconds)

    async def bounded(self, kind, call):
        if kind == 'principal':
            self.principal_reads += 1
        fence = self.principal_reads - 1
        self.timeline.append((kind, round(time.monotonic() - self.started, 3)))
        try:
            async with asyncio.timeout(self.remaining()):
                await asyncio.sleep(self.delay(kind, fence))
                return await call()
        except TimeoutError:
            raise c.CaptureError('timeout') from None

    async def read(self, kind):
        return await self.bounded(kind, partial(super().read, kind))

    async def configuration(self, identifier):
        return await self.bounded('config', partial(super().configuration, identifier))


class ReserveTests(LiveHarness):
    """HAMCP-235 review 1 (R1-M2): the reserve covers the final identity reads only."""

    # A tenth of the shipped bounds: 4.5 s collection, 0.5 s reads.
    SCALED = {'COLLECTION_SECONDS': 4.5, 'READ_SECONDS': 0.5,
              'FINAL_FENCE_RESERVE_MIN': 0.5, 'FINAL_FENCE_RESERVE_MAX': 1.5}

    def deadline(self, count, delay):
        service, _ = self.service(fixture(count))
        client = DeadlineClient(service.provider.client.data, delay)
        service.provider.client = client
        return service, client

    async def timed(self, service, **bounds):
        began = time.monotonic()
        with patch.multiple(c, **{**self.SCALED, **bounds}):
            page = await self.capture(service)
        report, _ = service.store.get(capture_id(page), OWNER, service.now_ms())
        return report, time.monotonic() - began

    def test_the_shipped_reserve_bounds_cover_the_identity_reads(self):
        self.assertEqual(c.IDENTITY_READS, ('principal', 'entries', 'devices'))
        self.assertEqual(c.FENCE_READS[:len(c.IDENTITY_READS)], c.IDENTITY_READS)
        self.assertEqual((c.FINAL_FENCE_RESERVE_MIN, c.FINAL_FENCE_RESERVE_MAX),
                         (c.READ_SECONDS, len(c.IDENTITY_READS) * c.READ_SECONDS))

    async def test_the_reserve_is_twice_the_measured_identity_reads_within_bounds(self):
        def slow(kinds, seconds):
            return lambda kind, fence: seconds if fence == 0 and kind in kinds else 0
        for label, delay, bounds, low, high in (
                ('identity', slow({'devices'}, 0.3), {}, 0.6, 0.9),
                ('inventory_not_counted', slow({'states', 'registry'}, 0.3), {}, 0.5, 0.5),
                ('minimum', slow(set(), 0), {'FINAL_FENCE_RESERVE_MIN': 0.7}, 0.7, 0.7),
                ('maximum', slow({'principal', 'entries', 'devices'}, 0.3),
                 {'FINAL_FENCE_RESERVE_MAX': 1.0}, 1.0, 1.0)):
            with self.subTest(label):
                self.fresh_store()
                service, _ = self.deadline(2, delay)
                report, _ = await self.timed(service, **bounds)
                reserve = report['diagnostics']['final_fence_reserve_seconds']
                self.assertGreaterEqual(reserve, low)
                self.assertLessEqual(reserve, high)
                self.assertEqual(report['diagnostics']['final_fence'], 'complete')

    async def test_no_configuration_read_is_scheduled_or_outlasts_the_window(self):
        # Reads may last 5 s, so the four in-flight configuration reads would
        # hang to the collection bound; the window (4.0 s) cuts them
        # (unreadable, timeout), nothing is scheduled after it (timed_out),
        # and the final fence keeps its whole reserve and completes.
        service, client = self.deadline(6, lambda kind, fence: 30 if kind == 'config' else 0)
        report, elapsed = await self.timed(service, READ_SECONDS=5)
        diagnostics = report['diagnostics']
        statuses = Counter(row['configuration']['status'] for row in report['baseline']['records'])
        self.assertEqual(statuses, {'unreadable': c.CONCURRENCY, 'timed_out': 6 - c.CONCURRENCY})
        self.assertEqual(set(diagnostics['record_outcomes'].values()), {'timeout'})
        self.assertEqual(diagnostics['final_fence_reserve_seconds'], 0.5)
        self.assertEqual((diagnostics['final_fence'], diagnostics['final_fence_reason']), ('complete', None))
        self.assertEqual(report['baseline']['installation']['status'], 'established')
        configs = [at for kind, at in client.timeline if kind == 'config']
        final = [at for _, at in client.timeline[len(c.FENCE_READS) + len(configs):]]
        self.assertEqual(len(configs), c.CONCURRENCY, 'nothing is scheduled after the window')
        self.assertEqual(len(final), len(c.FENCE_READS))
        self.assertGreaterEqual(min(final), 4.0 - 0.01)
        self.assertLess(min(final), 4.0 + 0.25, 'in-flight reads end at the window')
        self.assertLess(elapsed, 4.5)

    async def test_a_slow_final_fence_still_records_identity(self):
        # The first fence's identity reads take 0.1 s each (reserve 0.6 s,
        # window 3.9 s); configuration reads hang to the window; the final
        # identity reads take 0.1 s each, and then an inventory read may hang.
        def hang(*slow):
            def delay(kind, fence):
                if kind == 'config':
                    return 30
                if kind in c.IDENTITY_READS:
                    return 0.1
                return 30 if fence and kind in slow else 0
            return delay
        for label, delay, final, reason in (
                ('slow_identity_reads_complete', hang(), 'complete', None),
                ('hanging_states_read', hang('states'), 'identity_only', 'timeout'),
                ('hanging_registry_read', hang('registry'), 'identity_only', 'timeout')):
            with self.subTest(label):
                self.fresh_store()
                service, _ = self.deadline(6, delay)
                report, elapsed = await self.timed(service, READ_SECONDS=5)
                diagnostics = report['diagnostics']
                self.assertEqual(set(diagnostics['record_outcomes'].values()), {'timeout'})
                self.assertEqual((diagnostics['final_fence'], diagnostics['final_fence_reason']), (final, reason))
                self.assertGreaterEqual(diagnostics['final_fence_reserve_seconds'], 0.6)
                self.assertLess(diagnostics['final_fence_reserve_seconds'], 0.9)
                self.assertEqual(report['baseline']['installation']['status'], 'established')
                self.assertEqual(report['baseline']['installation']['installation_id'],
                                 diagnostics['first_fence_installation_id'])
                self.assertLess(elapsed, 4.5 + 0.1)


class CoverageTests(LiveHarness):
    """HAMCP-235 review 1 (R1-M2): the paired 115-automation, 1 s-per-read fixture.

    Shipped bounds (45 s collection, 5 s reads, four readers). Identity reads
    take about 3 s, so the reserve is about 6 s and configuration reads end at
    about 39 s; 115 reads in rounds of four end at about 34 s and the final
    fence at about 39 s. The base revision read all 115 in 39.3 s; review 1's
    reserve read 100 and timed 15 out.
    """

    async def test_115_automations_at_one_second_per_read_are_all_read(self):
        service, _ = self.service(fixture(LIVE_COUNT))
        client = DeadlineClient(service.provider.client.data, lambda kind, fence: 1.0)
        service.provider.client = client
        began = time.monotonic()
        page = await self.capture(service)
        elapsed = time.monotonic() - began
        report, _ = service.store.get(capture_id(page), OWNER, service.now_ms())
        diagnostics = report['diagnostics']
        statuses = Counter(row['configuration']['status'] for row in report['baseline']['records'])
        self.assertEqual(statuses, {'readable': LIVE_COUNT})
        self.assertEqual(diagnostics['record_outcomes'], {})
        self.assertEqual((diagnostics['final_fence'], diagnostics['final_fence_reason']), ('complete', None))
        self.assertGreaterEqual(diagnostics['final_fence_reserve_seconds'], 6.0)
        self.assertLess(diagnostics['final_fence_reserve_seconds'], 6.5)
        self.assertLess(elapsed, c.COLLECTION_SECONDS)


class HistoricalWriterTests(LiveHarness):
    """HAMCP-235 review 1 (R1-M1): captures written by 2.4.1-beta.43 itself.

    ``fixtures/hamcp235_beta43_resume`` was generated by its ``generate.py``
    from the exact beta.43 commit; ``provenance.json`` records how.
    """

    FIXTURE = ROOT / 'tests' / 'fixtures' / 'hamcp235_beta43_resume'

    def setUp(self):
        super().setUp()
        self.provenance = json.loads((self.FIXTURE / 'provenance.json').read_text())
        self.now = self.provenance['now_ms'] / 1000
        source = self.FIXTURE / 'store' / 'audit-baselines-v1'
        target = self.governance / 'audit-baselines-v1'
        (target / 'records').mkdir(parents=True, mode=0o700)
        target.chmod(0o700)
        for path in sorted(source.rglob('*.json')):
            copied = target / path.relative_to(source)
            copied.write_bytes(path.read_bytes())
            copied.chmod(0o600)

    def snapshot(self):
        root = self.governance / 'audit-baselines-v1'
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in sorted(root.rglob('*.json'))}

    def legacy(self):
        return self.service(fixture(4))

    def test_the_fixture_is_the_recorded_beta43_output(self):
        self.assertEqual(self.provenance['writer_commit'], '0e0624297d32608bd4dfda36d90879b3462334fa')
        self.assertEqual(self.provenance['writer_version'], '2.4.1-beta.43')
        files = {path.relative_to(self.FIXTURE).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted((self.FIXTURE / 'store').rglob('*')) if path.is_file()}
        self.assertEqual(files, self.provenance['files'])
        captures = self.provenance['captures']
        self.assertEqual(captures['anchorless']['installation']['status'], 'unestablished')
        self.assertFalse(captures['anchorless']['first_fence_anchor_recorded'])
        self.assertEqual(captures['established']['installation']['status'], 'established')

    async def test_an_anchorless_beta43_capture_is_refused_twice_and_nothing_persists(self):
        key = self.provenance['captures']['anchorless']['capture_id']
        before = self.snapshot()
        for label, installation in (('same_installation', None), ('different_installation', 'device2')):
            with self.subTest(label):
                service, client = self.legacy()
                if installation:
                    client.data['devices'][0]['id'] = installation
                error = await self.reason(self.capture(service, resume_capture_id=key))
                self.assertEqual(error.details, {'reason': 'resume_inventory_changed',
                                                 'resume_changes': ['installation_anchor_missing']})
                self.assertFalse(error.retryable)
                self.assertEqual(client.calls, [], 'nothing is read before the refusal')
                self.assertFalse(service.active)
                self.assertEqual(self.snapshot(), before, 'nothing is persisted')

    async def test_an_established_beta43_capture_still_resumes_with_its_own_anchor(self):
        legacy = self.provenance['captures']['established']
        service, client = self.legacy()
        resumed = await self.capture(service, resume_capture_id=legacy['capture_id'])
        self.assertEqual(sorted(call for call in client.calls if type(call) is tuple), [('config', '1')])
        header = resumed['baseline_header']
        self.assertEqual(header['resume']['resumed_count'], 1)
        self.assertEqual(header['installation']['status'], 'established')
        self.assertEqual(header['installation']['installation_id'], legacy['installation']['installation_id'])
        self.assertEqual(resumed['record_outcomes'], {})
        service, client = self.legacy()
        client.data['devices'][0]['id'] = 'device2'
        error = await self.reason(self.capture(service, resume_capture_id=legacy['capture_id']))
        self.assertEqual(error.details, {'reason': 'resume_inventory_changed',
                                         'resume_changes': ['installation_changed']})


class ContractTests(unittest.TestCase):
    def test_refusal_details_carry_only_fixed_categories(self):
        error = c.CaptureError('resume_inventory_changed', resume_changes=(
            'mapping_changed', 'automation.secret_name', 'sha256:' + '0' * 64, 'automation_added', 'mapping_changed'))
        self.assertEqual(error.details, {'reason': 'resume_inventory_changed',
                                         'resume_changes': ['automation_added', 'mapping_changed']})
        self.assertEqual(c.CaptureError('timeout', resume_changes=('automation_added',)).details,
                         {'reason': 'timeout'})

    def test_resume_changes_compare_only_stable_identity(self):
        def record(key, entity, state='on'):
            return {'configuration_id': key, 'entity_id': entity,
                    'enabled_state': {'state': state}, 'configuration': {'status': 'readable'}}

        base = {'baseline': {'installation': {'status': 'unestablished', 'installation_id': None},
                             'records': [record('1', 'automation.a'), record('2', 'automation.b')]},
                'diagnostics': {'first_fence_installation_id': 'sha256:a'}}
        same = [record('1', 'automation.a', 'off'), record('2', 'automation.b', 'off')]
        changes = BaselineCaptureProvider.resume_changes
        self.assertEqual(changes(base, 'sha256:a', same), ())
        self.assertEqual(changes(base, 'sha256:b', same), ('installation_changed',))
        self.assertEqual(changes(base, 'sha256:a', [record('1', 'automation.a'), record('3', 'automation.c')]),
                         ('automation_added', 'automation_removed'))
        self.assertEqual(changes(base, 'sha256:a', [record('1', 'automation.a'), record('2', 'automation.x')]),
                         ('mapping_changed',))
        base['diagnostics'] = {}
        self.assertEqual(changes(base, 'sha256:b', same), (), 'no recorded anchor, no installation claim')


if __name__ == '__main__':
    unittest.main()
