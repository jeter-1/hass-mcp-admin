"""Offline HAMCP-315 harness boundaries; no Docker, network or production key."""
import argparse
import asyncio
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import core_helper_value_local as lane
import helper_value_contract_acceptance as acceptance
from core_release_evidence import groups, manifest, passing_subset, compare_sources
from ha_mcp_engineering.helper_value.contracts import HELPER_ACTIONS


class Contracts(unittest.TestCase):
    def test_every_domain_service_pair_has_a_real_case(self):
        self.assertEqual({(d, a) for d, a, _, _ in acceptance.CASES},
                         {(d, a) for d, actions in HELPER_ACTIONS.items() for a in actions})
        for domain, action, value, _ in acceptance.CASES:
            args = acceptance.helper_request(domain, action, value).arguments()
            self.assertFalse(args['wait']); self.assertFalse(args['return_response']); self.assertFalse(args['verbose'])

    def test_mapping_never_adds_unsigned_capability_or_lends_another_group(self):
        ref = acceptance.reference()
        self.assertEqual(groups()['helper_value'], [ref['capability_id']])
        self.assertEqual(passing_subset([], {'helper_value': True}), [])
        self.assertEqual(passing_subset([ref], {'general': True}), [])
        self.assertEqual(passing_subset([ref], {'helper_value': True}), [ref])

    def test_manifest_expands_with_complete_reviewed_helper_scopes(self):
        proof = json.loads((ROOT / 'docs/evidence/hamcp315/core-helper-source-hashes.json').read_text())
        paths = set(manifest()['profiles'][acceptance.CAPABILITY])
        self.assertEqual(paths, {row['path'] for row in proof['files']})
        self.assertTrue(paths <= set(manifest()['paths']))
        for domain in HELPER_ACTIONS:
            for file in ('__init__.py', 'entity.py', 'services.py', 'const.py', 'services.yaml'):
                self.assertIn(f'homeassistant/components/{domain}/{file}', paths)
        for domain in ('button', 'number', 'select', 'text'):
            self.assertIn(f'homeassistant/components/{domain}/entity.py', paths)

    def test_cached_image_requires_exact_repo_digest(self):
        def reply(digests):
            return subprocess.CompletedProcess([], 0, json.dumps([{'Id': 'config', 'RepoDigests': digests}]).encode())
        with patch.object(lane, 'command', return_value=reply(['repo@sha256:other'])):
            with self.assertRaisesRegex(lane.Refusal, 'cached_image_digest_mismatch'):
                lane.cached_image('repo:tag@sha256:exact')
        with patch.object(lane, 'command', return_value=reply(['repo@sha256:exact'])):
            self.assertEqual(lane.cached_image('repo:tag@sha256:exact'), 'config')

    def test_layout_absences_compare_equal_under_owner_amendment(self):
        proof = json.loads((ROOT / 'docs/evidence/hamcp315/manifest-compatibility.json').read_text())
        for item in proof['sources']:
            # Synthetic identical hashes test the comparison logic, not source provenance.
            source = {'version': item['version'], 'commit': item['source_commit'],
                      'tag_commit': item['source_commit'],
                      'hashes': {p: 'a' * 64 for p in manifest()['paths'] if p not in item['missing_paths']}}
            compared = compare_sources(source, source)
            self.assertTrue(compared['inherited'])
            self.assertEqual(compared['differing_files'], [])

    def test_inspection_error_is_not_absence_and_wrong_owner_is_refused(self):
        failed = subprocess.CompletedProcess([], 1, b'')
        with patch.object(lane, 'command', side_effect=[failed, lane.Refusal('daemon_failed')]):
            with self.assertRaises(lane.Refusal): lane.inspect_owned('container', 'owned', 'id')
        data = {'Config': {'Labels': {lane.LABEL: 'another-run'}}}
        with patch.object(lane, 'command', return_value=subprocess.CompletedProcess([], 0, json.dumps([data]).encode())):
            with self.assertRaisesRegex(lane.Refusal, 'resource_ownership_mismatch'):
                lane.inspect_owned('container', 'owned', 'id')



class Orchestration(unittest.TestCase):
    def run_lane(self, *, exercise_error=None, cleanup_error=False, dirty=False, existing=False,
                 admission_error=None, memory_args=()):
        calls, resources = [], {}
        def command(*args, **kwargs):
            calls.append(args)
            stdout, code = b'', 0
            if args[:2] == ('git', 'status'):
                stdout = b' M source.py' if dirty else b''
            elif args[:2] == ('git', 'rev-parse'): stdout = b'a' * 40
            elif 'inspect' in args:
                code = 0 if existing else 1
            elif args[:3] == ('docker', 'network', 'create'): resources[lane.NETWORK] = True
            elif args[:2] == ('docker', 'run'): resources[lane.CORE] = True
            elif args[:2] == ('docker', 'rm') or args[:3] == ('docker', 'network', 'rm'):
                resources.pop(args[-1], None)
            return subprocess.CompletedProcess(args, code, stdout)
        def inspect(kind, name, run_id):
            return {} if name in resources else None
        def remove(args, *_):
            if cleanup_error: raise lane.Refusal('cleanup_failed')
            shutil.rmtree(args.directory)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / '.artifacts').mkdir()
            out = root / '.artifacts' / 'run'
            argv = ['--mode', 'local', '--pull', 'never', '--journal', 'journal',
                    '--source-archive', 'archive', '--run-dir', str(out), *memory_args]
            with patch.dict(os.environ, {}, clear=True), patch.object(lane, 'ROOT', root), \
                 patch.object(lane, 'command', side_effect=command), \
                 patch.object(lane, 'inputs', return_value={}), \
                 patch.object(lane, 'images', return_value=('core@sha256:a', 'up@sha256:b', lane.host.FIXTURE_IMAGE)), \
                 patch.object(lane, 'cached_image', return_value='config'), \
                 patch.object(lane.resources, 'wait_for_admission', side_effect=admission_error,
                              return_value={'other_heavy_jobs': 0}), \
                 patch.object(lane, 'sha256', return_value='c' * 64), \
                 patch.object(lane, 'inspect_owned', side_effect=inspect), \
                 patch.object(lane.host, 'local_remove', side_effect=remove), \
                 patch.object(lane, 'require_free_ports'), patch.object(lane.signal, 'signal'), \
                 patch.object(lane, 'exercise', new=AsyncMock(side_effect=exercise_error,
                     return_value={'result': 'PASS'})), patch('sys.stdout', new_callable=io.StringIO):
                if dirty or existing:
                    with self.assertRaises(lane.Refusal): lane.main(argv)
                    self.assertFalse(out.exists())
                    self.assertFalse(any(c[:2] == ('docker', 'run') or c[:2] == ('docker', 'rm') for c in calls))
                    return
                code = lane.main(argv)
                result = json.loads((out / 'helper_value.json').read_text())
                if admission_error:
                    self.assertEqual(code, 1)
                    self.assertFalse(result['execution_started'])
                    self.assertEqual(result['reason'], 'host_admission_timeout')
                    self.assertTrue(result['cleanup']['clean'])
                    self.assertFalse(resources)
                    self.assertFalse(any(c[:2] == ('docker', 'run') for c in calls))
                    return
            self.assertFalse(resources)
            self.assertIn('started_utc', result); self.assertIn('ended_utc', result)
            for call in calls:
                if call[:2] == ('docker', 'run'):
                    self.assertEqual(call[call.index('--pull') + 1], 'never')
                    self.assertIn('127.0.0.1:18123:8123', call)
                    self.assertIn('--memory', call)
                    self.assertEqual(call[call.index('--memory') + 1], '3g' if memory_args else '2g')
                self.assertNotIn('pull', call[1:2])
            return code, result

    def test_admission_timeout_creates_refusal_receipt_without_containers(self):
        self.run_lane(admission_error=lane.resources.Refusal('host_admission_timeout'))

    def test_explicit_memory_override(self):
        code, result = self.run_lane(memory_args=('--core-memory', '3g', '--upstream-memory', '1g',
                                                 '--fixture-memory', '256m'))
        self.assertEqual(code, 0)
        self.assertEqual(result['container_memory'], {'core': '3g', 'upstream': '1g', 'fixture': '256m'})

    def test_pass_requires_cleanup_true(self):
        code, result = self.run_lane()
        self.assertEqual(code, 0); self.assertTrue(result['cleanup']['clean'])

    def test_lane_failure_withholds_and_still_cleans(self):
        code, result = self.run_lane(exercise_error=AssertionError('synthetic-private-text'))
        self.assertEqual(code, 1); self.assertEqual(result['result'], 'WITHHELD')
        self.assertTrue(result['cleanup']['clean']); self.assertNotIn('synthetic-private-text', json.dumps(result))

    def test_cleanup_failure_overrides_pass(self):
        code, result = self.run_lane(cleanup_error=True)
        self.assertEqual(code, 1); self.assertEqual(result['result'], 'WITHHELD'); self.assertFalse(result['cleanup']['clean'])

    def test_dirty_checkout_never_starts(self): self.run_lane(dirty=True)
    def test_prior_run_is_preserved(self): self.run_lane(existing=True)


class UpstreamStartup(unittest.IsolatedAsyncioTestCase):
    async def test_local_start_is_explicitly_no_pull_and_labelled(self):
        import real_ha_contract_tests as harness
        commands = []
        async def docker(*args, **kwargs):
            commands.append(args)
            # Stop before health polling; env file must still be removed.
            raise RuntimeError('synthetic-stop')
        with patch.object(harness, 'UPSTREAM_IMAGE', 'repo@sha256:synthetic'), \
             patch.object(harness, '_docker_command', side_effect=docker):
            with self.assertRaisesRegex(RuntimeError, 'synthetic-stop'):
                await harness._start_exact_upstream('synthetic-token', pull_policy='never', labels=('owned=test',), memory='768m')
        call, = commands
        self.assertEqual(call[call.index('--pull') + 1], 'never')
        self.assertEqual(call[call.index('--label') + 1], 'owned=test')
        self.assertIn('--memory', call)
        self.assertEqual(call[call.index('--memory') + 1], '768m')
        self.assertFalse(Path(call[call.index('--env-file') + 1]).exists())
        self.assertNotIn('synthetic-token', repr(call))


class AcceptanceScript(unittest.IsolatedAsyncioTestCase):
    async def test_scenario_orchestration_with_synthetic_transport_is_not_core_evidence(self):
        """Exercise the script itself; authority/provenance are tested separately.

        Real F3 stores and provider decoding, synthetic transport/Core/gateway.
        This test cannot produce or stand in for a disposable PASS receipt.
        """
        from tests.test_typed_helper_value import HelperTransport, SignedCore
        from tests.test_typed_fan import Clock
        from datetime import datetime, timezone
        from tests.test_core_release_evidence import refs
        from core_registry_contract_lane import lane_entry, probe_profile_for
        transport = HelperTransport('8.5.0')
        transport.states = {key.split('.')[0] + '.hamcp_seq17': {**value,
            'entity_id': key.split('.')[0] + '.hamcp_seq17'}
            for key, value in transport.states.items() if key.endswith('.synthetic')}
        counter = transport.states['counter.hamcp_seq17']
        counter['state'] = '2'; counter['attributes']['initial'] = 2
        transport.states['input_boolean.absent_seq17'] = {
            'entity_id': 'input_boolean.absent_seq17', 'state': 'unavailable',
            'last_updated': 'synthetic-missing', 'attributes': {}}
        probe = probe_profile_for('2026.11.0')
        entry = {**lane_entry('2026.11.0'), 'capabilities': refs(), 'evidence_sha256': 'a' * 64,
                 'probe_profile_id': probe.profile_id, 'probe_profile_sha256': probe.contract_fingerprint}
        created = []
        def runtime():
            value = SignedCore(signed=bool(created))
            value.version = '2026.11.0'
            value.current_observation = SimpleNamespace(version=value.version)
            value.configure = lambda *a, **kw: None
            value.health_snapshot = lambda: {'fallback_count': 0}
            created.append(value)
            return value
        def provider(*args):
            return acceptance.ObservedProvider(transport, lambda *a: transport.authority)
        class Raw:
            def __init__(self, configured): pass
            async def request(self, method, path):
                assert method == 'GET' and str(path).startswith('/states/')
                return deepcopy(transport.states[str(path).split('/')[-1]])
        gateway = SimpleNamespace(configure=lambda *a, **kw: None, reconcile_until_initialized=AsyncMock())
        clock = Clock(); clock.value = datetime.now(timezone.utc)
        real_sleep = asyncio.sleep
        async def yield_only(seconds):
            clock.advance(seconds)
            await real_sleep(0)
        real_service = acceptance.HelperService
        def service(*args, **kwargs):
            return real_service(*args, **kwargs, now=clock, sleep=yield_only)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(acceptance, 'CoreRuntime', side_effect=runtime), \
             patch.object(acceptance, 'UpstreamReadGateway', return_value=gateway), \
             patch.object(acceptance.ObservedProvider, 'configured', side_effect=provider), \
             patch.object(acceptance, 'HomeAssistantRestClient', Raw), \
             patch.object(acceptance, 'HelperService', side_effect=service), \
             patch.object(acceptance, 'close_disposable_authority', new=AsyncMock()), \
             patch.object(acceptance.asyncio, 'sleep', side_effect=yield_only):
            result = await acceptance.run(SimpleNamespace(), entry, Path(directory))
        self.assertEqual(result['result'], 'PASS')
        self.assertTrue(result['reopened_recovery_read_only'])
        self.assertTrue(result['unverifiable_effect_not_success'])
        self.assertEqual(result['duplicate_writes'], 0)


if __name__ == '__main__': unittest.main()
