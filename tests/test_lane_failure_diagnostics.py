"""Failure evidence must be useful without exporting raw exception material."""
import asyncio
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import lane_failure_diagnostics as diagnostics
import core_helper_value_local as lane
from tests import test_core_helper_value_local as orchestration


class FailureReceipts(unittest.TestCase):
    def test_forced_each_stage_preserves_exception_and_innermost_check(self):
        cases = [('image_verify', 'cached_image_digest'), ('container_start', 'core_start'),
                 ('core_boot', 'onboarding_ready'), ('auth', 'bootstrap_http'),
                 ('helper_setup', 'authority_setup'), ('operation', 'operation_dispatch'),
                 ('readback', 'state_readback'), ('cleanup', 'container_remove')]
        for stage, check in cases:
            with self.subTest(stage=stage), diagnostics.lane('helper_value'), \
                    patch.object(diagnostics, '_raw_log', return_value=False):
                failure = RuntimeError('synthetic-private-message')
                try:
                    with diagnostics.check('helper_setup', 'helper_contract'):
                        with diagnostics.check(stage, check):
                            raise failure
                except RuntimeError as caught:
                    self.assertIs(caught, failure)
                    receipt = diagnostics.failure(caught)
                self.assertEqual((receipt['lane'], receipt['stage'], receipt['check']),
                                 ('helper_value', stage, check))
                self.assertEqual(receipt['exception_chain'][0]['type'], 'RuntimeError')

    def test_untrusted_messages_are_wholly_redacted(self):
        # Build the synthetic key marker at runtime so the source credential
        # scanner stays effective without mistaking this canary for a key.
        key_canary = '-----BEGIN ' + 'PRIVATE KEY-----CANARY'
        canaries = ['Bearer eyJhbGciOiJIUzI1NiJ9.canary.signature',
                    'password=CANARY_PASSWORD', 'token: CANARY_TOKEN',
                    'https://user:CANARY_CREDENTIAL@private.invalid/internal',
                    '/mcp/CANARY_SECRET_PATH', '/home/person/CANARY_PRIVATE_PATH',
                    'postgres://user:CANARY_DB_PASSWORD@192.168.1.4/db',
                    'ghp_CANARY_ACCESS_TOKEN', key_canary]
        for canary in canaries:
            for prefix in ('', next(iter(diagnostics.SAFE_MESSAGES)) + ' '):
                with self.subTest(canary=canary), patch.object(diagnostics, '_raw_log', return_value=False):
                    result = diagnostics.failure(RuntimeError(prefix + canary))
                    self.assertNotIn('CANARY', json.dumps(result))
                    self.assertNotIn(canary, json.dumps(result))
                    self.assertEqual(result['exception_chain'][0]['message'], '[redacted]')

    def test_chain_messages_and_frames_are_bounded_and_cycle_safe(self):
        error = None
        for _ in range(12):
            try:
                raise RuntimeError('X' * 10000) from error
            except RuntimeError as exc:
                error = exc
        with patch.object(diagnostics, '_raw_log', return_value=False):
            receipt = diagnostics.failure(error)
        self.assertEqual(len(receipt['exception_chain']), diagnostics.MAX_CHAIN)
        self.assertTrue(receipt['chain_truncated'])
        for item in receipt['exception_chain']:
            self.assertLessEqual(len(item['message']), diagnostics.MAX_MESSAGE)
            self.assertLessEqual(len(item['frames']), diagnostics.MAX_FRAMES)
            for frame in item['frames']:
                self.assertFalse(Path(frame['file']).is_absolute())
                self.assertIsInstance(frame['line'], int)
        error.__cause__ = error
        with patch.object(diagnostics, '_raw_log', return_value=False):
            self.assertEqual(len(diagnostics.failure(error)['exception_chain']), 1)

    def test_timeout_wrapper_keeps_inner_stage(self):
        inner = asyncio.CancelledError()
        inner._local_lane_check = ('core_boot', 'runtime_ready')
        outer = TimeoutError()
        outer.__cause__ = inner
        with patch.object(diagnostics, '_raw_log', return_value=False):
            result = diagnostics.failure(outer)
        self.assertEqual((result['stage'], result['check']), ('core_boot', 'runtime_ready'))
        self.assertEqual([item['type'] for item in result['exception_chain']],
                         ['TimeoutError', 'CancelledError'])

    def test_unprintable_exception_cannot_replace_lane_failure(self):
        class Broken(Exception):
            def __str__(self): raise RuntimeError('private')
        with patch.object(diagnostics, '_raw_log', return_value=False):
            self.assertEqual(diagnostics.failure(Broken())['exception_chain'][0]['message'], '[redacted]')

    def test_raising_frame_is_repo_relative_without_source_or_locals(self):
        try:
            lane.host.memory_bytes('invalid')
        except Exception as exc:
            with patch.object(diagnostics, '_raw_log', return_value=False):
                receipt = diagnostics.failure(exc)
        self.assertEqual(receipt['exception_chain'][0]['frames'][-1]['file'],
                         'scripts/core_evidence_host.py')
        self.assertEqual(set(receipt['exception_chain'][0]['frames'][-1]), {'file', 'line'})

    def test_raw_retention_is_private_outside_repository_and_never_in_receipt(self):
        # Explicit exception to repo-local scratch: raw diagnostics must be outside it.
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory, \
                patch.object(diagnostics, 'RAW_DIRECTORY', Path(directory)), \
                patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            try:
                raise RuntimeError('CANARY_LOCAL_ONLY')
            except RuntimeError as exc:
                result = diagnostics.failure(exc, extra=lambda f: f.write('CANARY_CONTAINER_LOG'))
            self.assertTrue(result['raw_log_retained'])
            files = list(Path(directory).iterdir())
            self.assertEqual(len(files), 1)
            self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
            self.assertIn('CANARY_LOCAL_ONLY', files[0].read_text())
            self.assertIn('CANARY_CONTAINER_LOG', files[0].read_text())
            self.assertNotIn('CANARY', json.dumps(result))
            self.assertNotIn(directory, json.dumps(result))
            self.assertNotIn(files[0].name, json.dumps(result))
            # Later cleanup of unrelated disposable resources cannot remove this file.
            self.assertTrue(files[0].exists())

    def test_raw_refuses_repository_actions_symlink_and_insecure_directory(self):
        with patch.object(diagnostics, 'RAW_DIRECTORY', ROOT / '.artifacts/raw'), \
                patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            self.assertFalse(diagnostics._raw_log(RuntimeError(), None))
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            path = Path(directory)
            with patch.object(diagnostics, 'RAW_DIRECTORY', path), \
                    patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
                self.assertFalse(diagnostics._raw_log(RuntimeError(), None))
                self.assertEqual(list(path.iterdir()), [])
            link = path / 'link'; link.symlink_to(path, target_is_directory=True)
            with patch.object(diagnostics, 'RAW_DIRECTORY', link), \
                    patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
                self.assertFalse(diagnostics._raw_log(RuntimeError(), None))
            path.chmod(0o755)
            with patch.object(diagnostics, 'RAW_DIRECTORY', path), \
                    patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
                self.assertFalse(diagnostics._raw_log(RuntimeError(), None))

    def test_pass_receipt_exact_frozen_bytes_and_no_raw_log(self):
        with patch.object(lane, 'utc', return_value='2026-11-06T00:00:00+00:00'), \
                patch.object(lane.diagnostics, 'failure') as failure:
            code, receipt = orchestration.Orchestration().run_lane()
        self.assertEqual(code, 0); failure.assert_not_called()
        # HAMCP-326 adds only the approved posture to the original frozen receipt.
        expected = {'model': 'core-helper-value-local-evidence-v1', 'group': 'helper_value',
                    'result': 'PASS', 'started_utc': '2026-11-06T00:00:00+00:00',
                    'ended_utc': '2026-11-06T00:00:00+00:00', 'source_commit': 'a' * 40,
                    'journal_sha256': lane.JOURNAL_SHA256, 'manifest_sha256': 'c' * 64,
                    'host_admission': {'other_heavy_jobs': 0},
                    'network_posture': {'driver': 'bridge', 'host_bind_address': '127.0.0.1', 'egress_allowed': True},
                    'container_memory': {'core': '2g', 'upstream': '768m', 'fixture': '128m'},
                    'mode': 'local', 'pull': 'never', 'cleanup': {'clean': True,
                    'containers_absent': True, 'network_absent': True, 'configuration_absent': True}}
        self.assertEqual(lane.canonical(receipt), lane.canonical(expected))

    def test_actual_container_start_failure_is_identified_and_cleaned(self):
        for command, check in [(('docker', 'network', 'create'), 'network_create'),
                               (('docker', 'run'), 'core_start')]:
            with self.subTest(check=check):
                code, result = orchestration.Orchestration().run_lane(command_failure=command)
                self.assertEqual(code, 1)
                self.assertEqual((result['failure']['stage'], result['failure']['check']),
                                 ('container_start', check))
                self.assertTrue(result['cleanup']['clean'])

    def test_actual_cached_image_failure_is_identified(self):
        with diagnostics.lane('helper_value'), patch.object(lane, 'command', side_effect=RuntimeError('private')):
            with self.assertRaises(RuntimeError) as caught:
                lane.cached_image('synthetic-image')
        self.assertEqual(caught.exception._local_lane_check, ('image_verify', 'cached_image_digest'))

    def test_primary_failure_survives_cleanup_failure(self):
        code, result = orchestration.Orchestration().run_lane(exercise_error=RuntimeError('private'), cleanup_error=True)
        self.assertEqual(code, 1)
        self.assertEqual(result['failure']['exception_chain'][0]['type'], 'RuntimeError')
        self.assertEqual(result['cleanup']['failure']['stage'], 'cleanup')
        self.assertEqual(result['cleanup']['failure']['check'], 'configuration_remove')


class StartupChecks(unittest.IsolatedAsyncioTestCase):
    async def test_actual_onboarding_guard_has_precise_identity(self):
        import real_ha_contract_tests as harness
        class Response:
            status = 503
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        class Session(Response):
            def get(self, *_args, **_kwargs): return Response()
        with patch.object(harness.aiohttp, 'ClientSession', return_value=Session()), \
                patch.object(harness.asyncio, 'sleep', new=AsyncMock()), diagnostics.lane('helper_value'):
            with self.assertRaises(RuntimeError) as caught:
                await harness.bootstrap_disposable_admin()
        self.assertEqual(caught.exception._local_lane_check, ('core_boot', 'onboarding_ready'))

    async def test_actual_runtime_guard_has_precise_identity(self):
        import real_ha_contract_tests as harness
        rest = AsyncMock(); rest.request.return_value = {'state': 'STARTING', 'components': []}
        with patch.object(harness.asyncio, 'sleep', new=AsyncMock()), diagnostics.lane('helper_value'):
            with self.assertRaises(RuntimeError) as caught:
                await harness.wait_for_runtime_ready(rest)
        self.assertEqual(caught.exception._local_lane_check, ('core_boot', 'runtime_ready'))
        self.assertEqual(rest.request.await_count, 120)

    async def test_actual_auth_http_failure_is_identified(self):
        import real_ha_contract_tests as harness
        class Response:
            status = 401
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        class Session:
            def request(self, *args, **kwargs): return Response()
        with diagnostics.lane('helper_value'):
            with self.assertRaises(RuntimeError) as caught:
                await harness._json_request(Session(), 'POST', '/synthetic')
        self.assertEqual(caught.exception._local_lane_check, ('auth', 'bootstrap_http'))

    async def test_actual_helper_setup_failure_is_identified(self):
        import helper_value_contract_acceptance as acceptance
        with diagnostics.lane('helper_value'):
            with self.assertRaises(AssertionError) as caught:
                await acceptance.run(None, {'capabilities': []}, Path('.'))
        self.assertEqual(caught.exception._local_lane_check, ('helper_setup', 'helper_contract'))

    async def test_instrumentation_inert_outside_local_scope(self):
        error = RuntimeError('unchanged')
        @diagnostics.checked('auth', 'bootstrap')
        async def fail(): raise error
        with self.assertRaises(RuntimeError) as caught: await fail()
        self.assertIs(caught.exception, error)
        self.assertFalse(hasattr(error, '_local_lane_check'))
