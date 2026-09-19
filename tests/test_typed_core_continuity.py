"""Ephemeral signed applicability; real Core coordinator, providers and F3.

Synthetic source identities and transports are not release compatibility evidence.
No production keys, endpoints, devices, or registry records are used.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hass_mcp_engineering_beta'), str(ROOT / 'tests')]

from core_registry_fixtures import CoreSigner, ProjectedCoreSource, core_entry, core_revocation, NOW
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_core_readmission.registry_models import CoreRegistryEnvelope
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_TYPED_OPERATION_PROFILES, CORE_CAPABILITY_PROFILES
from ha_mcp_engineering.fan.contracts import FanRequest, digest
from ha_mcp_engineering.fan.service import FanService
from ha_mcp_engineering.fan.authority import FanCoreAuthority
from ha_mcp_engineering.providers.upstream_fan import FanProvider
from ha_mcp_engineering.power.contracts import PowerRequest
from ha_mcp_engineering.power.service import PowerService, PowerCoreAuthority
from ha_mcp_engineering.providers.upstream_power import PowerProvider
from ha_mcp_engineering.request_context import begin_request, end_request
from tests.test_typed_fan import Clock, Transport
from tests.test_typed_power import PowerTransport


class TypedCoreContinuityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.signer = CoreSigner()
        self.entries = [core_entry(v, typed_operations=True) for v in ('2026.9.2', '2026.9.3')]
        for entry in self.entries:
            entry['image_index_digest'] = 'sha256:' + sha256(('synthetic-image-' + entry['version']).encode()).hexdigest()
        self.raw = self.signer.journal_raw(entries=self.entries)
        self.now = NOW
        async def fetch(*args):
            return self.raw
        self.registry = CoreReleaseRegistry(enabled=True, public_key=self.signer.public_key_base64,
                                            cache_path=self.root/'registry.json', fetcher=fetch,
                                            now=lambda: self.now)
        self.core = CoreRuntime()
        self.source = ProjectedCoreSource(self.registry, '2026.9.3', typed_operations=True)
        self.core.configure(object(), source=self.source, release_registry=self.registry)
        self.clock = Clock()
        self.telemetry, token = begin_request('synthetic-typed-continuity')
        self.addCleanup(end_request, token)
        self.services = []

    async def runtime(self):
        await self.core.reconcile_once('test')

    async def publish(self, entries, revocations=()):
        previous = json.loads(self.raw)
        tip = CoreRegistryEnvelope.from_mapping(previous['envelopes'][-1])
        envelope = self.signer.raw(sequence=tip.sequence+1, previous_registry_sha256=tip.content_digest,
                                   generated_at=self.now, entries=entries, revocations=list(revocations))
        self.raw = self.signer.journal_raw(envelopes=[*previous['envelopes'], envelope])
        self.assertTrue(await self.registry.refresh())

    def operation(self, family='fan', provider_version='8.5.0', action='turn_on'):
        if family == 'fan':
            transport = Transport()
            transport.catalog = PowerTransport(provider_version).catalog
            provider = FanProvider(transport, lambda *args: transport.authority)
            service = FanService(self.root/uuid.uuid4().hex, provider, FanCoreAuthority(self.core), now=self.clock)
            request = FanRequest(entity_id='fan.synthetic', action=action,
                                 percentage=50 if action != 'turn_off' else None,
                                 operation_id=f'{int(self.clock().timestamp())}-{uuid.uuid4().hex}')
        else:
            transport = PowerTransport(provider_version)
            provider = PowerProvider(transport, lambda *args: transport.authority)
            service = PowerService(self.root/uuid.uuid4().hex, provider, PowerCoreAuthority(self.core), now=self.clock)
            request = PowerRequest(entity_id=family+'.synthetic', action=action,
                                   operation_id=f'{int(self.clock().timestamp())}-{uuid.uuid4().hex}')
        self.addAsyncCleanup(service.close)
        self.services.append(service)
        setattr(self.telemetry, service.binding_attribute, digest(request.model_dump()))
        return service, transport, request

    def settled(self):
        health = self.core.health_snapshot()
        for key in ('issued_lease_count', 'active_commit_count', 'fallback_count'):
            self.assertEqual(health[key], 0, key)
        for service in self.services:
            self.assertEqual(service.locks.records(), ())

    async def test_same_executable_two_signed_core_releases_both_providers_all_domains(self):
        for version in ('2026.9.2', '2026.9.3'):
            self.source.version = version
            await self.runtime()
            self.assertEqual(self.core.health_snapshot()['compatible_count'], 19)
            for provider_version in ('8.4.3', '8.5.0'):
                for family in ('fan', 'light', 'switch'):
                    with self.subTest(core=version, provider=provider_version, family=family):
                        service, transport, request = self.operation(family, provider_version)
                        first = await service.control(request)
                        self.assertEqual(first['state'], 'succeeded_verified', first)
                        self.assertEqual(first['core_binding']['version'], version)
                        self.assertEqual(first['provider_contract'], f'ha-mcp-{provider_version}-single-{service.core.family}-v2')
                        self.assertEqual(first['provider'], service.provider_name)
                        self.assertEqual(first['fallback'], 'none')
                        self.assertEqual(await service.control(request), first)
                        self.assertEqual(transport.writes, 1)
                        self.assertEqual(service.load(request.task_id).core_binding, first['core_binding'])
        self.settled()

    async def test_another_exact_release_needs_data_only(self):
        self.source.version = '2099.1.7'
        await self.publish([core_entry('2099.1.7', typed_operations=True)])
        await self.runtime()
        for family in ('fan', 'switch'):
            service, transport, request = self.operation(family)
            result = await service.control(request)
            self.assertEqual(result['state'], 'succeeded_verified')
            self.assertEqual(result['core_binding']['version'], '2099.1.7')
            self.assertEqual(transport.writes, 1)
        self.settled()

    async def test_existing_seventeen_references_never_grant_new_release_actions(self):
        self.raw = self.signer.journal_raw(entries=[core_entry('2026.9.3')])
        await self.runtime()
        for family in ('fan', 'light', 'switch'):
            service, transport, request = self.operation(family)
            with self.assertRaisesRegex(ValueError, 'core_authority'):
                await service.control(request)
            self.assertEqual(transport.calls, [])
        self.settled()

    async def test_legacy_core2_behavior_without_new_signed_references_is_preserved(self):
        self.source.version = '2026.9.2'
        self.raw = self.signer.journal_raw(entries=[core_entry()])
        await self.runtime()
        for family in ('fan', 'switch'):
            service, transport, request = self.operation(family)
            result = await service.control(request)
            self.assertEqual(result['state'], 'succeeded_verified')
            self.assertTrue(result['provider_contract'].startswith('core-2026.9.2-'))
            self.assertNotIn('core_binding', result)
        self.settled()

    async def test_wrong_fingerprint_cannot_grant_operation(self):
        for reference in self.entries[1]['capabilities']:
            if reference['capability_id'].startswith('core.typed_'):
                reference['contract_fingerprint'] = 'sha256:' + '0'*64
        self.raw = self.signer.journal_raw(entries=self.entries)
        await self.runtime()
        for family in ('fan', 'switch'):
            service, transport, request = self.operation(family)
            with self.assertRaisesRegex(ValueError, 'core_authority'):
                await service.control(request)
            self.assertEqual(transport.calls, [])
        self.settled()

    async def test_one_family_reference_does_not_authorize_the_other(self):
        self.entries[1]['capabilities'] = [p for p in self.entries[1]['capabilities']
                                          if p['capability_id'] != 'core.typed_power_operation']
        self.raw = self.signer.journal_raw(entries=self.entries)
        await self.runtime()
        fan, transport, request = self.operation()
        self.assertEqual((await fan.control(request))['state'], 'succeeded_verified')
        power, transport, request = self.operation('switch')
        with self.assertRaisesRegex(ValueError, 'core_authority'):
            await power.control(request)
        self.assertEqual(transport.calls, [])
        self.settled()

    async def test_core_change_before_dispatch_refuses_even_when_both_are_signed(self):
        await self.runtime()
        for family in ('fan', 'switch'):
            self.source.version = '2026.9.3'; await self.runtime()
            service, transport, request = self.operation(family)
            async def change(tool):
                if tool == 'ha_call_service':
                    self.source.version = '2026.9.2'
                    await self.runtime()
            transport.before = change
            result = await service.control(request)
            self.assertNotEqual(result['state'], 'succeeded_verified')
            self.assertEqual(transport.writes, 0)
        self.settled()

    async def test_revocation_before_dispatch_never_uses_legacy_or_other_provider(self):
        await self.runtime()
        service, transport, request = self.operation()
        async def revoke(tool):
            if tool == 'ha_call_service':
                await self.publish([], [core_revocation('2026.9.3')])
        transport.before = revoke
        result = await service.control(request)
        self.assertNotEqual(result['state'], 'succeeded_verified')
        self.assertEqual(transport.writes, 0)
        self.settled()

    async def test_response_loss_is_verified_by_readback_and_never_redispatched(self):
        await self.runtime()
        for family in ('fan', 'switch'):
            service, transport, request = self.operation(family)
            transport.mode = 'timeout'
            result = await service.control(request)
            self.assertEqual(result['state'], 'succeeded_verified', result)
            self.assertFalse(result['provider_response_received'])
            await service.control(request)
            self.assertEqual(transport.writes, 1)
        self.settled()

    async def test_binding_tampering_and_unknown_contract_refuse_without_dispatch(self):
        await self.runtime()
        for family in ('fan', 'switch'):
            service, transport, request = self.operation(family)
            prepared = await service.adapter.prepare(request)
            service.save(prepared)
            path = service._path(request.task_id)
            original = json.loads(path.read_bytes())
            for field, value in [('core_binding', {**original['core_binding'], 'version':'2026.9.2'}),
                                 ('core_binding', None), ('provider_contract', 'unreviewed')]:
                corrupt = deepcopy(original); corrupt[field] = value
                path.write_text(json.dumps(corrupt))
                with self.assertRaisesRegex(ValueError, 'receipt_corrupt'):
                    service.load(request.task_id)
            path.write_text(json.dumps(original))
            self.assertEqual(transport.writes, 0)
        self.settled()

    async def test_forged_core_version_on_lease_cannot_be_consumed(self):
        await self.runtime()
        guard = FanCoreAuthority(self.core)
        authority = await guard.acquire(SimpleNamespace(target=SimpleNamespace(
            target_type='fan', target_id='fan.synthetic')))
        self.assertIsNotNone(authority)
        self.assertIsNone(guard.consume(replace(authority, core_version='2026.9.2')))
        guard.release(authority)
        self.settled()

    async def test_unknown_release_expiry_and_invalid_signature_never_reach_provider(self):
        for failure in ('unknown', 'expired', 'signature'):
            with self.subTest(failure=failure):
                if failure == 'unknown':
                    self.source.version = '2026.9.4'
                elif failure == 'expired':
                    self.source.version = '2026.9.3'
                    self.now = NOW + timedelta(days=2)
                else:
                    # Existing authority remains expired; another signer's
                    # bytes must not manufacture a replacement grant.
                    self.raw = CoreSigner().journal_raw(entries=self.entries)
                    self.assertFalse(await self.registry.refresh())
                await self.runtime()
                for family in ('fan', 'switch'):
                    service, transport, request = self.operation(family)
                    with self.assertRaisesRegex(ValueError, 'core_authority'):
                        await service.control(request)
                    self.assertEqual(transport.calls, [])
        self.settled()

    async def test_admitted_core_does_not_relax_provider_catalog_or_identity(self):
        await self.runtime()
        for family in ('fan', 'switch'):
            for drift in ('version', 'schema', 'protocol'):
                service, transport, request = self.operation(family)
                changes = {'version': {'server_version':'99.0.0'}, 'schema': {'tools':()},
                           'protocol': {'protocol_version':'unreviewed'}}[drift]
                transport.catalog = replace(transport.catalog, **changes)
                with self.assertRaisesRegex(ValueError, 'provider_'):
                    await service.control(request)
                self.assertEqual(transport.calls, [])
        self.settled()

    async def test_noop_is_verified_without_dispatch_under_signed_operation_authority(self):
        await self.runtime()
        for family in ('fan', 'light', 'switch'):
            service, transport, request = self.operation(family, action='turn_off')
            result = await service.control(request)
            self.assertEqual(result['state'], 'succeeded_verified')
            self.assertEqual(result['provider_attempt_count'], 0)
            self.assertFalse(result['dispatch_intent_recorded'])
            self.assertEqual(transport.writes, 0)
        self.settled()

    async def test_generation_retirement_invalidates_consumed_lease(self):
        await self.runtime()
        guard = FanCoreAuthority(self.core)
        target = SimpleNamespace(target=SimpleNamespace(target_type='fan', target_id='fan.synthetic'))
        authority = await guard.acquire(target)
        commits = guard.consume(authority)
        self.assertIsNotNone(commits)
        self.source.version = '2026.9.2'
        await self.runtime()
        self.assertFalse(guard.revalidate(authority, commits))
        guard.finish(commits)
        self.settled()

    async def test_failed_signed_acquisition_does_not_retry_a_legacy_contract(self):
        self.source.version = '2026.9.2'
        await self.runtime()
        guard = FanCoreAuthority(self.core)
        target = SimpleNamespace(target=SimpleNamespace(target_type='fan', target_id='fan.synthetic'))
        with patch.object(self.core, 'acquire', return_value=None) as acquire:
            self.assertIsNone(await guard.acquire(target))
        self.assertEqual(acquire.call_count, 1)
        self.assertIn('core.typed_fan_operation', acquire.call_args.args[0])

    async def test_new_audit_records_attribute_exact_core_and_provider_contract(self):
        from ha_mcp_engineering.audit import AuditLogger
        await self.runtime()
        service, transport, request = self.operation()
        path = self.root/'synthetic-audit.jsonl'
        service.executions.audit = AuditLogger(str(path), 'synthetic-test-only-audit-secret')
        receipt = await service.control(request)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        snapshots = [row for row in rows if row.get('event') == 'fan_execution_snapshot']
        self.assertTrue(snapshots)
        for row in snapshots:
            self.assertEqual(row['core_binding'], receipt['core_binding'])
            self.assertEqual(row['provider_contract'], receipt['provider_contract'])
            self.assertEqual(row['fallback'], 'none')
        self.assertEqual(transport.writes, 1)
        self.settled()

    async def test_postdispatch_version_change_keeps_uncertainty_and_readonly_recovery(self):
        await self.runtime()
        service, transport, request = self.operation()
        original = transport.execute_read
        async def change_after_effect(tool, args, **kwargs):
            result = await original(tool, args, **kwargs)
            if tool == 'ha_call_service':
                self.source.version = '2026.9.2'
                await self.runtime()
            return result
        transport.execute_read = change_after_effect
        result = await service.control(request)
        self.assertNotEqual(result['state'], 'succeeded_verified', result)
        self.assertEqual(transport.writes, 1)
        # A fresh service instance simulates loss of the original owner.
        fresh = FanService(service.root.parent, service.provider, FanCoreAuthority(self.core), now=self.clock)
        self.addAsyncCleanup(fresh.close)
        unchanged = await fresh.control(request)
        self.assertNotEqual(unchanged['state'], 'succeeded_verified')
        self.assertEqual(transport.writes, 1)
        # Restore the exact bound identity within the existing readback window.
        self.source.version = '2026.9.3'; await self.runtime()
        self.clock.advance(121)
        recovered = await fresh.control(request)
        self.assertEqual(recovered['state'], 'succeeded_verified', recovered)
        self.assertEqual(transport.writes, 1)
        self.settled()

    def test_original_seventeen_fingerprints_are_not_redefined(self):
        journal = json.loads((ROOT/'upstream-trust/ha-core-release-registry.json').read_text())
        references = journal['envelopes'][-1]['entries'][0]['capabilities']
        compiled = {p.capability_id:p.contract_fingerprint for p in CORE_CAPABILITY_PROFILES}
        self.assertEqual({p['capability_id']:p['contract_fingerprint'] for p in references}, compiled)
        self.assertEqual(len(CORE_TYPED_OPERATION_PROFILES), 2)

    def test_exact_beta3_writer_declarations_preserve_contract_hash_and_bytes(self):
        folder = ROOT/'tests/fixtures/typed_beta3'
        provenance = json.loads((folder/'provenance.json').read_text())
        self.assertEqual(provenance['source_commit'], '349e0e49607e44a31eececd9ec457a5e124dbf98')
        self.assertEqual(sha256((folder/'generate.py').read_bytes()).hexdigest(), provenance['generator_sha256'])
        for record in provenance['records']:
            raw = (folder/record['file']).read_bytes()
            self.assertEqual(sha256(raw).hexdigest(), record['sha256'])
            cls = FanService if record['family'] == 'fan' else PowerService
            service = cls(self.root/record['file'], object(), object(), now=self.clock)
            path = service._path(record['task_id']); path.write_bytes(raw)
            prepared = service.load(record['task_id'])
            self.assertEqual(prepared.provider_contract, record['contract'])
            self.assertEqual(prepared.prepared_operation_hash, json.loads(raw)['prepared_hash'])
            self.assertIsNone(prepared.core_binding)
            service.save(prepared)
            self.assertEqual(path.read_bytes(), raw)
