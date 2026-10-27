"""HAMCP-226/228 review 2: the storage-boundary screen (R1, owner decision) and unreadable indexes (R2).

Synthetic only. The reviewer's six tests are committed unchanged as
``test_hamcp226_228_review2_delta_extra.py`` (pinned below); these cover the
rest of each correction:

- R1: every record and diagnostic is screened before hashing and before
  storage. A field the inner screens never see is withheld at the storage
  boundary, and the store refuses a report holding unscreened text.
- Owner decision (Josh, 2026-10-27): a hit in an optional field withholds that
  field and the capture is partial; a hit in the automation configuration id
  withholds that record as a stub with a reason and the capture continues; a
  hit in the fixed baseline header refuses before anything is stored.
- R2: an index that exists but cannot be read is rebuilt from the records
  when the namespace is writable; otherwise ``store_unavailable`` names the
  condition and no baseline file is touched.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]

import test_hamcp228_baseline_store as b  # noqa: E402
from test_automation_baseline_capture import SECRET, Client  # noqa: E402
from ha_mcp_engineering import untrusted_text  # noqa: E402
from ha_mcp_engineering.audit_baseline import capture_contracts as c  # noqa: E402
from ha_mcp_engineering.audit_baseline import storage_screen  # noqa: E402
from ha_mcp_engineering.audit_baseline.capture_provider import BaselineCaptureProvider  # noqa: E402
from ha_mcp_engineering.audit_baseline.capture_runtime import AUTOMATION_BASELINE_CAPTURE  # noqa: E402
from ha_mcp_engineering.audit_baseline.capture_service import BaselineCaptureService  # noqa: E402
from ha_mcp_engineering.audit_baseline.store import BaselineStore, StoreError  # noqa: E402
from ha_mcp_engineering.tools import audit_baseline as tool  # noqa: E402

REVIEWER_TEST = ROOT / 'tests' / 'test_hamcp226_228_review2_delta_extra.py'
# sha256 of test_delta_extra.py in the review bundle (.artifacts/reviews/pr317-219ae7e3).
REVIEWER_SHA256 = 'bc19a2582814137c022b2ff283dc2a38cf66ea93fdf8096ea02db1fa0dbebd7e'
NEEDLE = 'syntheticStorageNeedle317'
CREDENTIAL_URL = 'postgresql' + ':' + '//user:' + NEEDLE + '@host.invalid/db'
KEY_HEADER = 'BEGIN' + 'PRIVATE' + 'KEY'
ENTITY_NEEDLE = 'syntheticentityneedle317'
NOT_ROOT = unittest.skipIf(os.geteuid() == 0, 'root ignores the file modes these tests depend on')


def disk(service):
    return b''.join(path.read_bytes() for path in service.store.root.rglob('*') if path.is_file())


def files(service):
    return {path.name: path.read_bytes() for path in service.store.records.iterdir()}


class ReviewerTestFileTests(unittest.TestCase):
    def test_the_reviewers_tests_are_committed_byte_for_byte(self):
        self.assertEqual(hashlib.sha256(REVIEWER_TEST.read_bytes()).hexdigest(), REVIEWER_SHA256)


class Base(b.Harness):
    async def tool_capture(self, service):
        with patch.object(AUTOMATION_BASELINE_CAPTURE, 'require', return_value=service):
            return json.loads(await tool.capture_automation_baseline(limit=100))

    def injected(self, service, change):
        original = service.provider.collect

        async def collect(*args, **kwargs):
            report = await original(*args, **kwargs)
            change(report)
            return report
        return patch.object(service.provider, 'collect', collect)

    def stored_report(self, service, response):
        key = response['data']['snapshot']['capture_id']
        report, entry = service.store.get(key, b.OWNER, service.now_ms())
        self.assertEqual(c.digest(report['baseline']), response['data']['artifact_sha256'])
        self.assertEqual(entry['artifact_sha256'], response['data']['artifact_sha256'])
        return report


class StorageBoundaryScreenTests(Base):
    async def test_a_new_record_field_is_withheld_at_the_storage_boundary_and_the_capture_continues(self):
        service, _ = self.service()

        def change(report):
            report['baseline']['records'][0]['future_field'] = {'nested': [CREDENTIAL_URL]}
            # The stub's outcome goes with its record, or resume would retry an
            # identifier the stored baseline no longer holds.
            report['diagnostics']['record_outcomes']['0'] = 'timeout'
        with self.injected(service, change):
            response = await self.tool_capture(service)
        self.assertTrue(response['success'], response)
        self.assertEqual(response['metadata']['completeness'], 'partial')
        data = response['data']
        self.assertEqual([row['configuration_id'] for row in data['records']], ['1', '2'])
        self.assertEqual(data['diagnostics']['credential_screen'], {
            'withheld_field_count': 0, 'withheld_record_count': 1,
            'withheld_records': [{'configuration_id': '0', 'entity_id': 'automation.a0',
                                  'reason': 'record_unrepresentable'}]})
        inventory = data['baseline_header']['inventory']
        self.assertEqual((inventory['completeness'], inventory['declared_count'], inventory['omitted_count']),
                         ('partial', 3, 1))
        self.assertTrue(any('credential screen withheld 1 record(s)' in item for item in response['warnings']))
        self.assertNotIn(NEEDLE.encode(), disk(service))
        self.assertNotIn(NEEDLE, json.dumps(response))
        self.assertEqual(self.stored_report(service, response)['diagnostics']['record_outcomes'], {})

    async def test_an_optional_record_field_is_withheld_and_its_record_kept_partial(self):
        service, _ = self.service()

        def change(report):
            report['baseline']['records'][1]['entity_id'] = 'automation.' + ENTITY_NEEDLE
        known = (*service.provider.projector.known_secrets, ENTITY_NEEDLE)
        with self.injected(service, change), patch.object(service.provider.projector, 'known_secrets', known):
            response = await self.tool_capture(service)
        self.assertTrue(response['success'], response)
        self.assertEqual(response['metadata']['completeness'], 'partial')
        rows = response['data']['records']
        self.assertEqual([row['configuration_id'] for row in rows], ['0', '1', '2'])
        self.assertEqual((rows[1]['entity_id'], rows[1]['mapping_status']), (None, 'missing'))
        coverage = rows[1]['configuration']['coverage']
        self.assertEqual((coverage['redacted'], coverage['completeness'], coverage['warning_count']),
                         (True, 'partial', 1))
        self.assertEqual(rows[1]['configuration']['status'], 'readable')
        self.assertFalse(rows[0]['configuration']['coverage']['redacted'])
        screen = response['data']['diagnostics']['credential_screen']
        self.assertEqual((screen['withheld_field_count'], screen['withheld_record_count']), (1, 0))
        self.assertNotIn(ENTITY_NEEDLE.encode(), disk(service))
        report = self.stored_report(service, response)
        self.assertIsNone(report['baseline']['records'][1]['entity_id'])

    async def test_a_diagnostic_leaf_and_key_are_withheld_not_refused(self):
        service, _ = self.service()

        def change(report):
            report['diagnostics']['future_field'] = {'nested': [CREDENTIAL_URL], CREDENTIAL_URL: 'value'}
        with self.injected(service, change):
            response = await self.tool_capture(service)
        self.assertTrue(response['success'], response)
        self.assertEqual(response['metadata']['completeness'], 'partial')
        diagnostics = response['data']['diagnostics']
        self.assertEqual(diagnostics['future_field'], {'nested': [untrusted_text.credentials.WITHHELD_MARKER]})
        self.assertEqual(diagnostics['credential_screen']['withheld_field_count'], 2)
        self.assertEqual(len(response['data']['records']), 3)
        self.assertNotIn(NEEDLE.encode(), disk(service))
        self.stored_report(service, response)

    async def test_a_record_outcome_keyed_by_a_withheld_identifier_is_dropped(self):
        service, _ = self.service()

        def change(report):
            report['diagnostics']['record_outcomes'][CREDENTIAL_URL] = 'timeout'
            report['diagnostics']['record_outcomes']['1'] = CREDENTIAL_URL
        with self.injected(service, change):
            response = await self.tool_capture(service)
        self.assertTrue(response['success'], response)
        self.assertEqual(response['data']['record_outcomes'], {'1': untrusted_text.credentials.WITHHELD_MARKER})
        self.assertEqual(response['data']['diagnostics']['credential_screen']['withheld_field_count'], 2)
        self.assertNotIn(NEEDLE.encode(), disk(service))

    async def test_a_baseline_header_hit_refuses_before_anything_is_hashed_or_stored(self):
        service, _ = self.service()
        await self.capture(service)
        before = files(service)
        index = service.store.index_path.read_bytes()

        for path in (('inventory', 'limitations'), ('consistency', 'limitations')):
            with self.subTest(path=path):
                def change(report, path=path):
                    report['baseline'][path[0]][path[1]].append(CREDENTIAL_URL)
                with self.injected(service, change), patch.object(service, '_encode') as encode:
                    error = await self.reason(self.capture(service))
                self.assertEqual(error.reason, 'redacted_identity')
                encode.assert_not_called()
                self.assertEqual(files(service), before)
                self.assertEqual(service.store.index_path.read_bytes(), index)
        self.assertNotIn(NEEDLE.encode(), disk(service))

    async def test_the_screened_report_is_what_is_hashed(self):
        service, _ = self.service()
        seen = []
        encode = service._encode

        def spy(report):
            seen.append(copy.deepcopy(report))
            return encode(report)

        def change(report):
            report['diagnostics']['future_field'] = CREDENTIAL_URL
        with self.injected(service, change), patch.object(service, '_encode', spy):
            page = await self.capture(service)
        self.assertEqual(seen[0]['diagnostics']['future_field'], untrusted_text.credentials.WITHHELD_MARKER)
        self.assertEqual(page['artifact_sha256'], c.digest(seen[0]['baseline']))

    async def test_the_store_refuses_unscreened_content_even_without_the_service_screen(self):
        service, _ = self.service()
        await self.capture(service)
        before = files(service)
        entries = self.entries(service)

        # A known secret is only credential-shaped to the store when the
        # service passes the capture's known secrets on to it.
        for label, value in (('credential_url', CREDENTIAL_URL), ('known_secret', 'prefix ' + b.KNOWN)):
            with self.subTest(case=label):
                def change(report, value=value):
                    report['diagnostics']['future_field'] = {'nested': [value]}
                with self.injected(service, change), patch.object(storage_screen, 'screen_report',
                                                                  lambda report, known: report):
                    error = await self.reason(self.capture(service))
                self.assertEqual(error.reason, 'redacted_identity')
                self.assertEqual(files(service), before)
                self.assertEqual(self.entries(service), entries)
        self.assertNotIn(NEEDLE.encode(), disk(service))
        self.assertNotIn(b.KNOWN.encode(), disk(service))

    async def test_store_put_refuses_a_withheld_value_or_key_before_opening_a_file(self):
        service, _ = self.service()
        page = await self.capture(service)
        report, _ = service.store.get(b.capture_id(page), b.OWNER, service.now_ms())
        before = files(service)
        index = service.store.index_path.read_bytes()
        leaf = copy.deepcopy(report)
        leaf['diagnostics']['future_field'] = CREDENTIAL_URL
        key = copy.deepcopy(report)
        key['diagnostics'][CREDENTIAL_URL] = 1
        known = copy.deepcopy(report)
        known['diagnostics']['future_field'] = 'prefix ' + b.KNOWN
        for label, value, secrets in (('leaf', leaf, ()), ('key', key, ()), ('known', known, (b.KNOWN,))):
            with self.subTest(case=label):
                with patch.object(service.store, 'transaction') as transaction, self.assertRaises(StoreError) as caught:
                    service.store.put(capture_id='d' * 32, caller=b.OWNER, encoded=c.canonical(value),
                                      artifact_sha256=c.digest(value['baseline']),
                                      baseline_id=value['baseline']['baseline_id'], resumed_from=None,
                                      now_ms=service.now_ms(), known_secrets=secrets)
                self.assertEqual(caught.exception.reason, 'unscreened_content')
                transaction.assert_not_called()
        self.assertEqual(files(service), before)
        self.assertEqual(service.store.index_path.read_bytes(), index)


class WithheldIdentityTests(Base):
    async def test_a_withheld_configuration_id_is_a_stub_with_a_reason_and_the_capture_continues(self):
        for label, value in (('key_header', KEY_HEADER), ('known_secret', b.KNOWN), ('credential_url', CREDENTIAL_URL)):
            with self.subTest(case=label):
                data = b.fixture(3)
                data['states'][0]['attributes']['id'] = value
                data['registry'][0]['unique_id'] = value
                data['configs'][value] = {'id': value}
                service, client = self.service(data)
                service.store = BaselineStore(self.governance / label)
                response = await self.tool_capture(service)
                self.assertTrue(response['success'], response)
                self.assertEqual(response['metadata']['completeness'], 'partial')
                diagnostics = response['data']['diagnostics']
                self.assertEqual(diagnostics['credential_screen'], {
                    'withheld_field_count': 0, 'withheld_record_count': 1,
                    'withheld_records': [{'configuration_id': None, 'entity_id': 'automation.a0',
                                          'reason': 'configuration_id_withheld'}]})
                self.assertEqual((diagnostics['unmapped_count'], diagnostics['unmapped_entities']), (0, []))
                self.assertEqual([row['configuration_id'] for row in response['data']['records']], ['1', '2'])
                inventory = response['data']['baseline_header']['inventory']
                self.assertEqual((inventory['completeness'], inventory['omitted_count']), ('partial', 1))
                self.assertTrue(any('credential screen withheld 1 record(s)' in item
                                    for item in response['warnings']))
                self.assertNotIn(('config', value), client.calls)
                self.assertNotIn(value.encode(), disk(service))
                self.assertNotIn(value, json.dumps(response))

    async def test_a_registry_unique_id_withheld_alone_is_a_stub_not_unmapped(self):
        # The state's configuration id is clean; only the registry's copy is withheld.
        data = b.fixture(3)
        data['registry'][0]['unique_id'] = KEY_HEADER
        service, _ = self.service(data)
        response = await self.tool_capture(service)
        self.assertTrue(response['success'], response)
        self.assertEqual(response['metadata']['completeness'], 'partial')
        diagnostics = response['data']['diagnostics']
        self.assertEqual(diagnostics['credential_screen']['withheld_records'],
                         [{'configuration_id': None, 'entity_id': 'automation.a0',
                           'reason': 'configuration_id_withheld'}])
        self.assertEqual((diagnostics['unmapped_count'], diagnostics['unmapped_entities']), (0, []))
        self.assertEqual([row['configuration_id'] for row in response['data']['records']], ['1', '2'])
        self.assertNotIn(KEY_HEADER.encode(), disk(service))

    async def test_an_unusable_but_clean_configuration_id_stays_unmapped(self):
        data = b.fixture(3)
        data['states'][0]['attributes']['id'] = 'not valid/id'
        service, _ = self.service(data)
        response = await self.tool_capture(service)
        diagnostics = response['data']['diagnostics']
        self.assertEqual((diagnostics['unmapped_count'], diagnostics['unmapped_entities']), (1, ['automation.a0']))
        self.assertEqual(diagnostics['credential_screen']['withheld_record_count'], 0)
        self.assertEqual(response['warnings'], [])

    async def test_a_withheld_entity_id_is_a_content_free_stub(self):
        data = b.fixture(3)
        entity = 'automation.' + ENTITY_NEEDLE
        data['states'][0]['entity_id'] = entity
        data['registry'][0]['entity_id'] = entity
        service = BaselineCaptureService(
            BaselineCaptureProvider(Client(data), self.core, known_secrets=(SECRET, b.KNOWN, ENTITY_NEEDLE)),
            BaselineStore(self.governance), wall=lambda: self.now)
        response = await self.tool_capture(service)
        self.assertTrue(response['success'], response)
        self.assertEqual(response['data']['diagnostics']['credential_screen']['withheld_records'],
                         [{'configuration_id': None, 'entity_id': None, 'reason': 'entity_id_withheld'}])
        inventory = response['data']['baseline_header']['inventory']
        self.assertEqual((inventory['declared_count'], inventory['omitted_count']), (3, 1))
        self.assertNotIn(ENTITY_NEEDLE.encode(), disk(service))

    async def test_more_withheld_records_than_listed_are_all_counted(self):
        data = b.fixture(20)
        for index in range(18):
            value = KEY_HEADER + str(index)
            data['states'][index]['attributes']['id'] = value
            data['registry'][index]['unique_id'] = value
        service, _ = self.service(data)
        response = await self.tool_capture(service)
        screen = response['data']['diagnostics']['credential_screen']
        self.assertEqual((screen['withheld_record_count'], len(screen['withheld_records'])),
                         (18, storage_screen.LISTED_STUBS))
        self.assertEqual(len(response['data']['records']), 2)

    async def test_more_records_stubbed_at_the_storage_screen_than_listed_are_all_counted(self):
        # The storage screen's own stubs, after the projector's, are bounded too.
        data = b.fixture(20)
        data['states'][19]['attributes']['id'] = KEY_HEADER + '19'
        data['registry'][19]['unique_id'] = KEY_HEADER + '19'
        service, _ = self.service(data)

        def change(report):
            for row in report['baseline']['records'][:17]:
                row['future_field'] = {'nested': [CREDENTIAL_URL]}
        with self.injected(service, change):
            response = await self.tool_capture(service)
        self.assertTrue(response['success'], response)
        screen = response['data']['diagnostics']['credential_screen']
        self.assertEqual((screen['withheld_record_count'], len(screen['withheld_records'])),
                         (18, storage_screen.LISTED_STUBS))
        self.assertEqual(screen['withheld_records'][0]['reason'], 'configuration_id_withheld')
        self.assertEqual(len(response['data']['records']), 2)
        self.assertEqual(response['data']['baseline_header']['inventory']['omitted_count'], 18)
        self.assertNotIn(NEEDLE.encode(), disk(service))
        stored = self.stored_report(service, response)['diagnostics']['credential_screen']
        self.assertEqual(len(stored['withheld_records']), storage_screen.LISTED_STUBS)

    async def test_a_clean_capture_reports_nothing_withheld_and_stays_complete(self):
        service, _ = self.service()
        response = await self.tool_capture(service)
        self.assertEqual(response['metadata']['completeness'], 'complete')
        self.assertEqual(response['data']['diagnostics']['credential_screen'],
                         {'withheld_field_count': 0, 'withheld_record_count': 0, 'withheld_records': []})
        self.assertEqual(response['warnings'], [])


class UnreadableIndexTests(Base):
    def unavailable(self, service, condition):
        with self.assertRaises(StoreError) as caught:
            service.store.get(self.key, b.OWNER, service.now_ms())
        self.assertEqual((caught.exception.reason, caught.exception.condition), ('store_unavailable', condition))

    async def asyncSetUp(self):
        self.service_, _ = self.service()
        self.keys = [b.capture_id(await self.capture(self.service_)) for _ in range(2)]
        self.key = self.keys[0]

    @NOT_ROOT
    async def test_an_unreadable_index_in_a_read_only_namespace_is_reported_and_nothing_is_touched(self):
        service = self.service_
        store = service.store
        before = files(service)
        store.index_path.chmod(0)
        store.root.chmod(0o500)
        try:
            self.unavailable(service, 'namespace_not_writable')
            error = await self.reason(service.load(self.key, b.OWNER))
            self.assertEqual(error.details, {'reason': 'store_unavailable', 'store_condition': 'namespace_not_writable'})
            self.assertTrue(error.retryable)
            self.assertEqual(stat.S_IMODE(store.index_path.stat().st_mode), 0)
            self.assertEqual(files(service), before)
        finally:
            store.root.chmod(0o700)
        # Writable again, the still unreadable index is rebuilt from the records.
        report, entry = store.get(self.key, b.OWNER, service.now_ms())
        self.assertEqual(files(service), before)
        self.assertEqual(entry['capture_id'], self.key)
        self.assertEqual(store.status['last_index_rebuild']['reason'], 'index_unreadable')

    @NOT_ROOT
    async def test_a_read_only_records_directory_blocks_the_rebuild(self):
        service = self.service_
        store = service.store
        (store.records / ('e' * 32 + '.json')).write_bytes(b'damagedSynthetic')
        before = files(service)
        store.index_path.unlink()
        store.records.chmod(0o500)
        try:
            self.unavailable(service, 'namespace_not_writable')
            self.assertFalse(store.index_path.exists())
            self.assertEqual(files(service), before)
        finally:
            store.records.chmod(0o700)

    @NOT_ROOT
    async def test_an_unreadable_record_during_a_rebuild_stops_with_nothing_changed(self):
        service = self.service_
        store = service.store
        before = files(service)
        store.index_path.unlink()
        path = store.records / (self.keys[1] + '.json')
        path.chmod(0)
        try:
            self.unavailable(service, 'record_unreadable')
            self.assertFalse(store.index_path.exists())
            self.assertTrue(path.exists())
        finally:
            path.chmod(0o600)
        self.assertEqual(files(service), before)
        self.assertEqual(store.load_index(service.now_ms())['recovery']['last_index_rebuild']['recovered'], 2)

    @NOT_ROOT
    async def test_an_unlistable_records_directory_is_reported(self):
        service = self.service_
        store = service.store
        store.index_path.write_bytes(b'broken-index')
        store.records.chmod(0o300)
        try:
            self.unavailable(service, 'namespace_unreadable')
        finally:
            store.records.chmod(0o700)
        self.assertEqual(store.get(self.key, b.OWNER, service.now_ms())[1]['capture_id'], self.key)

    async def test_a_failed_index_replacement_during_a_rebuild_removes_nothing(self):
        service = self.service_
        store = service.store
        damaged = 'e' * 32 + '.json'
        (store.records / damaged).write_bytes(b'damagedSynthetic')
        before = files(service)
        store.index_path.write_bytes(b'broken-index')

        def fault(stage):
            if stage == 'before_index':
                raise OSError('synthetic')
        store.fault_hook = fault
        with self.assertRaises(StoreError) as caught:
            store.get(self.key, b.OWNER, service.now_ms())
        self.assertEqual(caught.exception.reason, 'store_unavailable')
        self.assertEqual(files(service), before)
        self.assertEqual(store.index_path.read_bytes(), b'broken-index')
        store.fault_hook = None
        self.assertEqual(store.get(self.key, b.OWNER, service.now_ms())[1]['capture_id'], self.key)
        self.assertNotIn(damaged, files(service))

    def test_the_store_condition_detail_is_fixed_and_only_for_store_unavailable(self):
        self.assertEqual(c.CaptureError('store_unavailable', store_condition='record_unreadable').details,
                         {'reason': 'store_unavailable', 'store_condition': 'record_unreadable'})
        self.assertEqual(c.CaptureError('store_unavailable', store_condition='/synthetic/path').details,
                         {'reason': 'store_unavailable'})
        self.assertEqual(c.CaptureError('store_unavailable').details, {'reason': 'store_unavailable'})
        self.assertEqual(c.CaptureError('baseline_damaged', store_condition='record_unreadable').details,
                         {'reason': 'baseline_damaged'})


if __name__ == '__main__':
    unittest.main()
