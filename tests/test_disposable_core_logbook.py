"""Offline success/refusal controls for the isolated logbook acceptance lane."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('disposable_core_logbook', ROOT/'scripts/disposable_core_logbook.py')
lane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lane)


class DisposableLogbookTests(unittest.TestCase):
    def env(self):
        return {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'jeter-1/hass-mcp-admin',
                'GITHUB_EVENT_NAME':'push','GITHUB_REF':lane.BRANCH,'GITHUB_JOB':'core-logbook',
                'GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}

    def test_exact_execution_identity(self):
        self.assertEqual(lane.guard(self.env()), 'logbook-123-1')

    def test_wrong_repo_event_branch_job_and_ids_refused(self):
        for key,value in [('GITHUB_ACTIONS','false'),('GITHUB_REPOSITORY','other/repo'),
                          ('GITHUB_EVENT_NAME','pull_request'),('GITHUB_REF','refs/heads/main'),
                          ('GITHUB_JOB','publish'),('GITHUB_SHA','missing'),
                          ('GITHUB_RUN_ID','../1'),('GITHUB_RUN_ATTEMPT','')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                lane.guard({**self.env(),key:value})

    def events(self):
        return [{'entity_id':entity,'message':f'{entity}-{age}','age_hours':age}
                for age in (-1,6,18,48,120,192)
                for entity in ('switch.synthetic_alpha','switch.synthetic_beta')]

    def test_sparse_filtered_unfiltered_and_shifted_expectations(self):
        for hours in (12,24,72,168):
            for entity in ('','switch.synthetic_alpha'):
                for shift in (0,24):
                    chosen=[e for e in self.events() if shift<e['age_hours']<shift+hours
                            and (not entity or entity==e['entity_id'])]
                    self.assertEqual(lane.verify_records(chosen,self.events(),hours,entity,shift),
                                     sorted(e['message'] for e in chosen))

    def test_older_future_missing_duplicate_and_wrong_entity_refuse(self):
        good=[e for e in self.events() if e['age_hours']==6 and e['entity_id']=='switch.synthetic_alpha']
        for wrong in ([],good*2,good+[self.events()[0]],
                      [dict(good[0],entity_id='switch.synthetic_beta')],{}):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                lane.verify_records(wrong,self.events(),12,'switch.synthetic_alpha')

    def test_exact_source_binding_and_tamper(self):
        pins=json.loads(lane.PINS.read_text())
        lane.verify_unchanged_source(pins)
        with tempfile.TemporaryDirectory() as path:
            root=Path(path); (root/'reader.py').write_text('synthetic')
            with patch.object(lane,'ROOT',root):
                lane.verify_unchanged_source({'engineering_files':{'reader.py':lane.digest(b'synthetic')}})
                with self.assertRaises(ValueError):
                    lane.verify_unchanged_source({'engineering_files':{'reader.py':'0'*64}})

    def test_cleanup_refuses_foreign_owner_without_delete(self):
        with tempfile.TemporaryDirectory() as path, patch.object(lane,'docker') as docker:
            docker.side_effect=[subprocess.CompletedProcess([],0,b'logbook-123-1\n',b''),
                                subprocess.CompletedProcess([],0,b'other-owner\n',b'')]
            with self.assertRaises(ValueError):
                lane.cleanup('logbook-123-1',Path(path)/'logbook-123-1')
            self.assertEqual(docker.call_count,2)

    def test_cleanup_inventory_failure_is_not_absence(self):
        with tempfile.TemporaryDirectory() as path, patch.object(lane,'docker',side_effect=ValueError('docker_unavailable')):
            folder=Path(path)/'logbook-123-1';folder.mkdir()
            with self.assertRaises(ValueError):lane.cleanup('logbook-123-1',folder)
            self.assertTrue(folder.exists())

    def test_cleanup_owned_resources_and_private_fixture(self):
        identity='logbook-123-1'
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path)/identity; folder.mkdir(); (folder/'ephemeral-token').write_text('synthetic')
            calls=[];present={'container':True,'network':True}
            def docker(*args,**kw):
                calls.append(args)
                if 'ls' in args:
                    return subprocess.CompletedProcess([],0,(identity+'\n').encode() if present[args[0]] else b'',b'')
                if 'inspect' in args:
                    return subprocess.CompletedProcess([],0,(identity+'\n').encode(),b'')
                present['container' if args[0]=='rm' else 'network']=False
                return subprocess.CompletedProcess([],0,b'',b'')
            with patch.object(lane,'docker',docker):
                lane.cleanup(identity,folder)
            self.assertFalse(folder.exists())
            self.assertIn(('rm','-f',identity),calls)
            self.assertIn(('network','rm',identity),calls)

    def test_command_failure_and_output_bounds(self):
        with patch.object(lane.subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'',b'')):
            with self.assertRaises(ValueError):lane.command(['synthetic'])
        with patch.object(lane.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'x'*(lane.MAX_BYTES+1),b'')):
            with self.assertRaises(ValueError):lane.command(['synthetic'])

    def test_failure_reasons_are_fixed_not_exception_text(self):
        for reason,expected in [('registered_tool_failure','registered_tool_failure'),
                                ('synthetic-private-marker','unclassified')]:
            with self.assertRaises(lane.RequirementFailure) as caught:
                lane.require(False,reason)
            self.assertEqual(caught.exception.reason,expected)
            self.assertEqual(str(caught.exception),expected)

    def test_response_diagnostics_retain_only_known_synthetic_labels(self):
        secret='synthetic-private-marker'
        response={'success':True,'error_code':secret,'error':secret,'message':secret,
                  'data':[{'message':'synthetic_alpha_6','name':secret},
                          {'message':secret,'entity_id':secret},secret]*30}
        projected=lane.response_diagnostic(response)
        self.assertNotIn(secret,json.dumps(projected))
        self.assertEqual(projected['record_count'],90)
        self.assertEqual(projected['error_code'],'other_or_absent')
        self.assertEqual(set(projected['known_messages']),{'synthetic_alpha_6'})
        self.assertEqual(len(projected['known_messages'])+projected['unrecognized_records_in_first64'],64)
        import sys
        with patch.object(sys,'path',[str(ROOT/'hass_mcp_engineering_beta'),*sys.path]):
            from ha_mcp_engineering.models.responses import FailureResponse
        failed=FailureResponse(operation='get_logbook',error=secret,
                               error_code='home_assistant_unavailable',message=secret).as_dict()
        projected=lane.response_diagnostic(failed)
        self.assertEqual(projected['error_code'],'home_assistant_unavailable')
        self.assertFalse(projected['success'])
        self.assertNotIn(secret,json.dumps(projected))

    def test_workflow_only_branch_read_permissions_and_safe_artifact_list(self):
        import yaml
        workflow=yaml.safe_load((ROOT/'.github/workflows/beta10-core-logbook.yml').read_text())
        self.assertEqual(workflow[True],{'push':{'branches':[lane.BRANCH.removeprefix('refs/heads/')]}})
        self.assertEqual(workflow['permissions'],{'contents':'read'})
        steps=workflow['jobs']['core-logbook']['steps']
        cleanup=[s for s in steps if '--cleanup' in s.get('run','')]
        self.assertEqual(cleanup[0]['if'],'always()')
        artifact=steps[-1]['with']['path']
        self.assertNotIn('*',artifact)
        self.assertNotIn('ephemeral-token',artifact)
        self.assertNotIn('config/',artifact)
        for step in steps:
            if 'uses' in step:self.assertRegex(step['uses'],r'@[0-9a-f]{40}$')


class RegisteredDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_driver_context_and_response_contract_with_synthetic_peer(self):
        # This proves the driver, not Core. Actual Core execution remains CI-only.
        from aiohttp import web
        now=datetime(2026,9,30,12,tzinfo=timezone.utc)
        events=[{'entity_id':entity,'message':f'{entity}-{age}','age_hours':age,
                 'time':(now-timedelta(hours=age)).isoformat()}
                for age in (192,120,48,18,6,-1)
                for entity in ('switch.synthetic_alpha','switch.synthetic_beta')]
        calls=[]
        async def serve(request):
            calls.append(request.path_qs)
            start=datetime.fromisoformat(request.match_info['start'])
            end=datetime.fromisoformat(request.query['end_time']) if 'end_time' in request.query else start+timedelta(days=1)
            entity=request.query.get('entity')
            return web.json_response([{'entity_id':e['entity_id'],'message':e['message']}
                                      for e in events if start<datetime.fromisoformat(e['time'])<end
                                      and (not entity or entity==e['entity_id'])])
        app=web.Application();app.router.add_get('/api/logbook/{start}',serve)
        runner=web.AppRunner(app);await runner.setup()
        try:
            await web.TCPSite(runner,'127.0.0.1',18123).start()
            with tempfile.TemporaryDirectory() as path, patch.dict(os.environ):
                folder=Path(path);(folder/'ephemeral-token').write_text('synthetic-test-token')
                out=folder/'out';out.mkdir()
                await lane.reader_cases(folder,out,{'now':now.isoformat(),'events':events})
                result=json.loads((out/'interval-results.json').read_text())
                self.assertEqual(result['registered_case_count'],16)
                self.assertEqual(len(calls),17)
                self.assertEqual(result['status'],'PASS')
                self.assertEqual(result['missing_end_time_control'],'PASS')
        finally:
            await runner.cleanup()


class CoreFixtureStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_loader_state_exists_before_configuration_bootstrap(self):
        # Startup orchestration only; these synthetic modules are not Core.
        import asyncio
        from types import ModuleType, SimpleNamespace
        from unittest.mock import AsyncMock

        spec=importlib.util.spec_from_file_location('core_fixture',ROOT/'scripts/core_logbook_fixture.py')
        fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
        modules={name:ModuleType(name) for name in (
            'homeassistant','homeassistant.loader','homeassistant.bootstrap',
            'homeassistant.core','homeassistant.const','homeassistant.components',
            'homeassistant.components.recorder','homeassistant.components.recorder.tasks')}
        modules['homeassistant'].__file__='/synthetic/homeassistant/__init__.py'
        modules['homeassistant'].loader=modules['homeassistant.loader']
        modules['homeassistant.const'].__version__='synthetic'
        hass=SimpleNamespace(config=SimpleNamespace(),data={},async_stop=AsyncMock())
        modules['homeassistant.core'].HomeAssistant=lambda path:hass
        modules['homeassistant.loader'].async_setup=lambda target:target.data.update(components={},integrations={})
        class ConfigurationReached(Exception):
            pass
        async def configure(config,target):
            # Exact Core loader.py initializes these caches before bootstrap.
            self.assertEqual(target.data['components'],{})
            self.assertEqual(target.data['integrations'],{})
            raise ConfigurationReached
        modules['homeassistant.bootstrap'].async_from_config_dict=configure
        modules['homeassistant.components.recorder'].get_instance=lambda target:None
        modules['homeassistant.components.recorder.tasks'].CommitTask=object
        pins=SimpleNamespace(read_text=lambda:json.dumps({'core_version':'synthetic','core_files':{}}))
        def source_path(value):
            return pins if value=='/fixture/pins.json' else Path(value)
        loop=asyncio.get_running_loop()
        with patch.dict('sys.modules',modules), patch.object(fixture,'Path',source_path), \
             patch.object(fixture.logging,'disable'), patch.object(fixture.os,'umask'), \
             patch.object(loop,'add_signal_handler'):
            with self.assertRaises(ConfigurationReached):
                await fixture.main()
        hass.async_stop.assert_awaited_once()


if __name__=='__main__':
    unittest.main()
