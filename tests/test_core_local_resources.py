"""Offline resource admission and launch boundaries; never contact Docker/systemd."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import core_local_resources as guard
import core_evidence_host as host
import core_helper_value_local as lane


def observation(**values):
    return {'utc': 'synthetic', 'other_heavy_jobs': 1, 'mem_available_bytes': 3_500_000_000,
            'load1': 7.99, 'unclassified_containers': [], **values}


class Guard(unittest.TestCase):
    def test_admit_at_resource_boundaries(self):
        self.assertEqual(guard.refusals(observation()), [])
        self.assertEqual(guard.refusals(observation(other_heavy_jobs=0, load1=0)), [])

    def test_refuse_each_condition(self):
        for values, reason in (
            ({'other_heavy_jobs': 2}, 'other_heavy_jobs_exceed_one'),
            ({'mem_available_bytes': 3_499_999_999}, 'available_memory_below_3_5_GB'),
            ({'load1': 8}, 'load1_not_below_8'),
            ({'load1': float('nan')}, 'load1_not_below_8'),
            ({'unclassified_containers': ['unowned']}, 'container_lane_ownership_unknown'),
        ):
            with self.subTest(values=values):
                self.assertEqual(guard.refusals(observation(**values)), [reason])

    def test_lane_counted_once(self):
        containers = [{'id': name, 'labels': {guard.LANE_LABELS[0]: 'one-lane'}}
                      for name in ('core', 'upstream', 'fixture')]
        jobs, unknown = guard.lane_jobs(containers)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(unknown, [])
        containers.append({'id': 'other', 'labels': {guard.LANE_LABELS[0]: 'second-lane'}})
        self.assertEqual(len(guard.lane_jobs(containers)[0]), 2)

    def test_sampler_combines_one_evidence_job_and_one_multicontainer_lane(self):
        containers = [{'id': name, 'labels': {guard.LANE_LABELS[0]: 'one-lane'}}
                      for name in ('core', 'upstream')]
        with patch.object(guard, 'read_command', side_effect=[
                'core\nupstream\n', '\n'.join(json.dumps(row) for row in containers)]), \
             patch.object(guard, 'evidence_jobs', return_value=['other.service']), \
             patch.object(Path, 'read_text', return_value='MemAvailable: 4000000 kB\n'), \
             patch.object(guard.os, 'getloadavg', return_value=(1, 2, 3)):
            value = guard.sample()
        self.assertEqual(value['other_heavy_jobs'], 2)
        self.assertEqual(value['container_count'], 2)
        self.assertEqual(value['mem_available_bytes'], 4_096_000_000)
        self.assertEqual(value['load1'], 1)
        self.assertEqual(guard.refusals(value), ['other_heavy_jobs_exceed_one'])

    def test_unknown_or_ambiguous_ownership_is_not_assumed_idle(self):
        for labels in ({}, {guard.LANE_LABELS[0]: 'a', guard.LANE_LABELS[1]: 'b'}):
            self.assertEqual(guard.lane_jobs([{'id': 'x', 'labels': labels}]), ([], ['x']))

    def test_evidence_deduplicated_by_service_and_current_unit_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pid, unit in (('self', 'own.service'), ('10', 'other.service'), ('11', 'other.service'),
                              ('12', 'own.service')):
                path = root / pid; path.mkdir()
                (path / 'comm').write_text('pwsh\n')
                (path / 'cmdline').write_bytes(b'pwsh\0-File\0scripts/check.ps1\0-Tier\0Evidence\0')
                (path / 'cgroup').write_text('0::/user.slice/' + unit + '\n')
            def remap(value):
                return root / str(value).removeprefix('/proc/') if value != '/proc' else root
            with patch.object(guard, 'Path', side_effect=remap):
                self.assertEqual(guard.evidence_jobs(), ['other.service'])

    def wait(self, samples):
        now, sleeps = [0], []
        def sleep(seconds):
            sleeps.append(seconds); now[0] += seconds
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'admission.json'
            with patch.object(guard, 'utc', return_value='synthetic'):
                try:
                    value = guard.wait_for_admission(path, observe=samples, sleep=sleep, monotonic=lambda: now[0])
                except guard.Refusal as exc:
                    value = str(exc)
            return value, json.loads(path.read_text()), sleeps

    def test_wait_then_admit_records_metrics(self):
        samples = iter([observation(load1=8), observation()])
        value, receipt, sleeps = self.wait(lambda: next(samples))
        self.assertEqual(value, observation())
        self.assertEqual(receipt['status'], 'ADMITTED')
        self.assertEqual(len(receipt['checks']), 2)
        self.assertEqual(receipt['checks'][0]['load1'], 8)
        self.assertEqual(receipt['checks'][1]['mem_available_bytes'], 3_500_000_000)
        self.assertEqual(receipt['checks'][1]['other_heavy_jobs'], 1)
        self.assertEqual(sleeps, [600])

    def test_wait_times_out_at_sixty_minutes_with_clear_receipt(self):
        value, receipt, sleeps = self.wait(lambda: observation(other_heavy_jobs=2))
        self.assertEqual(value, 'host_admission_timeout')
        self.assertEqual(receipt['status'], 'REFUSED')
        self.assertEqual(len(receipt['checks']), 7)
        self.assertEqual(sleeps, [600] * 6)
        self.assertEqual(receipt['checks'][-1]['reasons'], ['other_heavy_jobs_exceed_one'])

    def test_observation_failure_refuses_without_waiting(self):
        def fail(): raise guard.Refusal('synthetic-private-error')
        value, receipt, sleeps = self.wait(fail)
        self.assertEqual(value, 'host_observation_failed')
        self.assertEqual(sleeps, [])
        self.assertEqual(receipt['checks'], [])
        self.assertNotIn('synthetic-private-error', json.dumps(receipt))


class Memory(unittest.TestCase):
    def test_fixture_run_has_memory(self):
        for code in (host.STAGE_CODE, host.REMOVE_CODE):
            try:
                argv = host.docker_argv(Path('/synthetic'), code)
            except host.Refusal as exc:
                self.fail('valid fixture run refused: ' + str(exc))
            self.assertIn('--memory', argv)
            self.assertEqual(argv[argv.index('--memory') + 1], '128m')
            self.assertEqual(argv[argv.index('--pull') + 1], 'never')
        custom = host.docker_argv(Path('/synthetic'), host.REMOVE_CODE, memory='256m', owner='a' * 32)
        self.assertEqual(custom[custom.index('--label') + 1], guard.LANE_LABELS[0] + '=' + 'a' * 32)
        self.assertEqual(custom[custom.index('--memory') + 1], '256m')

    def test_missing_zero_or_invalid_memory_refuses(self):
        for args in (['docker', 'run', 'image'], ['docker', 'run', '--memory'],
                     ['docker', 'run', '--memory', '0'], ['docker', 'run', '--memory', '0m'],
                     ['docker', 'run', '--memory', '1g', '--memory', '2g']):
            with self.subTest(args=args), self.assertRaises(host.Refusal):
                host.require_memory(args)
        for value in (None, '', '0', '0g', 'unlimited', '-1g', '1g --privileged'):
            with self.subTest(value=value), self.assertRaises(host.Refusal):
                host.memory_bytes(value)
        self.assertEqual(host.memory_bytes('2g'), 2 * 1024**3)
        self.assertEqual(host.memory_bytes('768m'), 768 * 1024**2)

    def test_core_command_without_limit_never_reaches_docker(self):
        with patch.object(lane.subprocess, 'run') as run:
            with self.assertRaises(host.Refusal): lane.command('docker', 'run', 'image')
            run.assert_not_called()
        with patch.object(lane.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            lane.command('docker', 'run', '--memory', '2g', 'image')
            run.assert_called_once()

    def test_upstream_local_command_without_limit_never_reaches_docker(self):
        import real_ha_contract_tests as harness
        with patch.dict(harness.os.environ, {'CORE_EVIDENCE_HOST_MODE': 'local'}), \
             patch.object(harness.asyncio, 'create_subprocess_exec', new_callable=AsyncMock) as spawn:
            with self.assertRaises(host.Refusal): asyncio.run(harness._docker_command('run', 'image'))
            spawn.assert_not_called()

    def test_runtime_unlimited_or_wrong_limit_refuses_before_acceptance(self):
        import real_ha_contract_tests as harness
        import helper_value_contract_acceptance as acceptance
        from types import SimpleNamespace
        for memory in (0, 1, None):
            data = {'Image': 'config', 'Config': {'Image': 'core'}, 'HostConfig': {'Memory': memory}}
            with patch.object(harness, 'bootstrap_disposable_admin', new=AsyncMock(return_value='synthetic')), \
                 patch.object(harness, 'settings'), patch.object(lane, 'replace', return_value=SimpleNamespace()), \
                 patch('ha_mcp_engineering.clients.HomeAssistantRestClient'), \
                 patch.object(harness, 'wait_for_runtime_ready', new=AsyncMock(return_value={'version': lane.VERSION})), \
                 patch.object(harness, '_start_exact_upstream', new_callable=AsyncMock), \
                 patch.object(lane, 'inspect_owned', return_value=data), \
                 patch.object(lane, 'cached_image', return_value='config'), \
                 patch.object(acceptance, 'run', new_callable=AsyncMock) as accept:
                with self.assertRaisesRegex(lane.Refusal, 'running_container_memory_mismatch'):
                    asyncio.run(lane.exercise(Path('/synthetic'), {}, 'core', 'upstream', 'id', '2g', '768m'))
                accept.assert_not_called()

    def test_upstream_missing_limit_refuses_before_credentials_or_start(self):
        import real_ha_contract_tests as harness
        with patch.object(harness, 'UPSTREAM_IMAGE', 'synthetic'), \
             patch.object(harness, '_docker_command', new_callable=AsyncMock) as run, \
             patch.object(harness.tempfile, 'NamedTemporaryFile') as file:
            with self.assertRaises(host.Refusal):
                asyncio.run(harness._start_exact_upstream('synthetic', pull_policy='never'))
            run.assert_not_called(); file.assert_not_called()


if __name__ == '__main__': unittest.main()
