"""Synthetic native capture -> frozen export -> unchanged offline comparator."""
import asyncio
from contextlib import asynccontextmanager
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'hass_mcp_engineering_beta'))
from ha_mcp_engineering.audit_baseline import capture_contracts as c
from ha_mcp_engineering.audit_baseline.capture_provider import BaselineCaptureProvider, Projector
from ha_mcp_engineering.audit_baseline.capture_service import BaselineCaptureService
from ha_mcp_engineering.audit_baseline import load_baseline, compare_baselines, canonical_configuration_digest
from ha_mcp_engineering.request_context import begin_request, end_request

SECRET = 'synthetic-never-export-credential'

def fixture(count=3):
    return {
        'principal': {'is_admin': True, 'name': SECRET, 'credentials': [SECRET], 'mfa_modules': [SECRET]},
        'entries': [{'entry_id': 'entry1', 'domain': 'hassio', 'state': 'loaded', 'data': SECRET}],
        'devices': [{'id': 'device1', 'config_entry_id': 'entry1', 'identifiers': [['hassio', 'core']], 'name': SECRET}],
        'states': [{'entity_id': f'automation.a{i}', 'state': 'off' if i==0 else 'on', 'attributes': {'id': str(i), 'extra': SECRET}} for i in range(count)],
        'registry': [{'entity_id': f'automation.a{i}', 'unique_id': str(i), 'platform': 'automation', 'disabled_by': None} for i in range(count)],
        'configs': {str(i): {'id': str(i), 'mode': 'single', 'actions': [{'variables': {'unicode': '\u00e9', 'number': 1.0, 'secret': SECRET}}]} for i in range(count)},
    }

class Core:
    def __init__(self):
        self.generation = 1
        self.available = True
        self.current_observation = SimpleNamespace(version='2026.9.4')
    def route_status(self, requirements):
        assert requirements == c.REQUIREMENTS
        return {'available': self.available, 'generation': self.generation}
    def health_projection(self):
        return {'compatible_count': 21, 'release_registry': {'sequence': 5}, 'counters': {'verification_failures': 0, 'retirements': 0}}

class Client:
    def __init__(self, data):
        self.data = data
        self.calls = []
        self.requests = self.consumed = self.active = self.maximum = 0
        self.hook = None
        self.closed = False
    @asynccontextmanager
    async def capture(self, version, authorize):
        self.authorize = authorize
        try:
            yield self
        finally:
            self.closed = True
    async def read(self, kind):
        self.authorize()
        self.calls.append(kind); self.requests += 1
        if self.hook:
            await self.hook(kind)
        return copy.deepcopy(self.data[kind])
    async def configuration(self, identifier):
        self.authorize()
        self.calls.append(('config', identifier)); self.requests += 1
        self.active += 1; self.maximum = max(self.maximum, self.active)
        try:
            await asyncio.sleep(0)
            if self.hook:
                await self.hook('config')
            value = self.data['configs'].get(identifier)
            if value is None:
                raise c.CaptureError('configuration_unavailable')
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)
        finally:
            self.active -= 1

async def export(service, limit=2):
    page = await service.capture(limit=limit)
    baseline = copy.deepcopy(page['baseline_header'])
    baseline['records'] = list(page['records'])
    digest = page['artifact_sha256']
    while page['pagination']['next_cursor']:
        page = await service.capture(limit=limit, cursor=page['pagination']['next_cursor'])
        assert page['baseline_header'] == {k:v for k,v in baseline.items() if k!='records'}
        assert page['artifact_sha256'] == digest
        baseline['records'].extend(page['records'])
    assert c.digest(baseline) == digest
    return baseline, page

class CaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.telemetry, self.token = begin_request()
        self.telemetry.caller_id = 'synthetic-owner'
        self.telemetry.core_dispatch_authorizer = lambda: True
        self.core = Core()
    def tearDown(self):
        end_request(self.token)
    def setup_capture(self, data=None, **kwargs):
        client = Client(fixture() if data is None else data)
        service = BaselineCaptureService(BaselineCaptureProvider(client, self.core, known_secrets=(SECRET,)), **kwargs)
        return service, client
    def validate(self, baseline):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'baseline.json'; path.write_bytes(c.canonical(baseline))
            return load_baseline(path)

    async def test_more_than_100_off_exact_hash_export_and_privacy(self):
        data = fixture(121)
        data['registry'].append({'entity_id': 'automation.disabled', 'unique_id': 'disabled', 'platform': 'automation', 'disabled_by': 'user'})
        service, client = self.setup_capture(data)
        baseline, page = await export(service, limit=17)
        self.validate(baseline)
        self.assertEqual(len(baseline['records']), 121)
        self.assertEqual(baseline['inventory']['completeness'], 'complete')
        self.assertEqual(baseline['records'][0]['enabled_state']['state'], 'off')
        self.assertEqual(baseline['records'][0]['configuration']['digest'], canonical_configuration_digest(data['configs']['0']))
        self.assertEqual(page['diagnostics']['registry_only_entities'], ['automation.disabled'])
        self.assertEqual(client.requests, 131)
        self.assertEqual(client.maximum, 4)
        self.assertNotIn(SECRET, c.canonical(page).decode())
        self.assertNotIn(SECRET, c.canonical(baseline).decode())
        self.assertTrue(client.closed)
        self.assertEqual(len(service.snapshots), 1)

    async def test_real_capture_pair_cli_all_classifications(self):
        first = fixture(5)
        service, client = self.setup_capture(first)
        before, _ = await export(service)
        second = fixture(5)
        second['states'] = [r for r in second['states'] if r['attributes']['id'] != '3']
        second['registry'] = [r for r in second['registry'] if r['unique_id'] != '3']
        second['states'].append({'entity_id':'automation.added','state':'on','attributes':{'id':'added'}})
        second['registry'].append({'entity_id':'automation.added','unique_id':'added','platform':'automation','disabled_by':None})
        second['configs']['added'] = {'id':'added'}
        second['configs']['1']['mode'] = 'restart'
        second['configs']['2'] = None
        second['states'][0]['entity_id'] = second['registry'][0]['entity_id'] = 'automation.renamed'
        second['states'][0]['state'] = 'on'
        client.data = second
        after, _ = await export(service)
        with tempfile.TemporaryDirectory() as directory:
            a,b = Path(directory)/'before.json',Path(directory)/'after.json'
            a.write_bytes(c.canonical(before)); b.write_bytes(c.canonical(after))
            command = [sys.executable, '-B', str(ROOT/'scripts/compare_automation_baselines.py'), str(a), str(b)]
            result = await asyncio.to_thread(subprocess.run, command, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            report=json.loads(result.stdout)
            self.assertEqual(report['counts'], {'ADDED':1,'REMOVED':1,'CHANGED':1,'UNCHANGED':2,'UNKNOWN':1,'TOTAL':6})
            self.assertEqual(report['entity_id_change_count'], 1)
            self.assertEqual(report['enabled_state_change_count'], 1)
            inverted=compare_baselines(load_baseline(b),load_baseline(a))
            self.assertEqual(inverted.counts['UNKNOWN'],6)

    async def test_unmapped_registry_omission_duplicate_id_and_unreadable(self):
        for scenario in ('omitted_registry','duplicate_id','unreadable','wrong_id','package_only'):
            with self.subTest(scenario=scenario):
                data=fixture()
                if scenario=='omitted_registry': data['registry'].pop(0)
                elif scenario=='duplicate_id': data['states'][1]['attributes']['id']='0'
                elif scenario=='wrong_id': data['configs']['0']['id']='other'
                else: data['configs']['0']=None
                service, _=self.setup_capture(data)
                baseline,page=await export(service)
                self.validate(baseline)
                if scenario in ('omitted_registry','duplicate_id'):
                    self.assertEqual(baseline['inventory']['completeness'],'partial')
                    self.assertGreater(baseline['inventory']['omitted_count'],0)
                else:
                    self.assertEqual(baseline['records'][0]['configuration']['status'],'unreadable')
                    self.assertEqual(page['diagnostics']['configuration_incomplete_count'],1)
                    self.assertIsNone(baseline['records'][0]['configuration']['digest'])

    async def test_identity_missing_ambiguous_wrong_binding_and_nonadmin_refused(self):
        for scenario in ('missing','duplicate','entry_mismatch','unloaded','nonadmin'):
            with self.subTest(scenario=scenario):
                data=fixture()
                if scenario=='missing': data['devices']=[]
                elif scenario=='duplicate': data['devices']*=2
                elif scenario=='entry_mismatch': data['devices'][0]['config_entry_id']='wrong'
                elif scenario=='unloaded': data['entries'][0]['state']='not_loaded'
                else: data['principal']['is_admin']=False
                service,client=self.setup_capture(data)
                with self.assertRaises(c.CaptureError): await service.capture()
                self.assertFalse(any(isinstance(call,tuple) for call in client.calls))
                self.assertEqual(service.snapshots,{})

    async def test_recreated_anchor_changes_lineage_and_clone_limitation_explicit(self):
        service,client=self.setup_capture()
        before,_=await export(service)
        client.data['devices'][0]['id']='recreated'
        after,_=await export(service)
        self.assertNotEqual(before['installation']['installation_id'],after['installation']['installation_id'])
        self.assertIn('restored_clones_may_share_identity',after['installation']['limitations'])
        result=compare_baselines(self.validate(before),self.validate(after))
        self.assertEqual(result.counts['UNKNOWN'],3)

    async def test_authority_principal_anchor_and_mapping_fence_changes(self):
        for scenario in ('authority','principal','anchor','mapping','inventory','missing_final'):
            with self.subTest(scenario=scenario):
                self.core=Core()
                service,client=self.setup_capture()
                async def change(kind):
                    if kind=='config':
                        if scenario=='authority': self.core.generation+=1
                        elif scenario=='principal': client.data['principal']['is_admin']=False
                        elif scenario=='anchor': client.data['devices'][0]['id']='changed'
                        elif scenario=='mapping': client.data['registry'][0]['unique_id']='changed'
                        elif scenario=='inventory': client.data['states'][0]['state']='on'
                    if scenario=='missing_final' and kind=='principal' and client.requests>5:
                        raise c.CaptureError('timeout')
                client.hook=change
                if scenario in ('authority','principal','anchor'):
                    with self.assertRaises(c.CaptureError): await service.capture()
                    self.assertEqual(service.snapshots,{})
                else:
                    baseline,_=await export(service)
                    self.validate(baseline)
                    if scenario=='missing_final':
                        self.assertEqual(baseline['installation']['status'],'unestablished')
                    else:
                        self.assertEqual(baseline['consistency']['inventory_drift'],'detected')

    async def test_cursor_frozen_caller_expiry_capacity_and_empty(self):
        clock=[100.0]
        service,client=self.setup_capture(clock=lambda:clock[0])
        page=await service.capture(limit=1)
        cursor=page['pagination']['next_cursor']; calls=len(client.calls)
        client.data=fixture(10)
        following=await service.capture(limit=1,cursor=cursor)
        self.assertEqual(following['pagination']['total_records'],3)
        self.assertEqual(len(client.calls),calls)
        self.telemetry.caller_id='other'
        with self.assertRaises(c.CaptureError): await service.capture(cursor=cursor)
        self.telemetry.caller_id='synthetic-owner'
        with self.assertRaises(c.CaptureError): await service.capture(cursor=cursor[:-1]+('1' if cursor[-1]!='1' else '0'))
        await service.capture()
        with self.assertRaises(c.CaptureError) as found: await service.capture()
        self.assertEqual(found.exception.reason,'capacity_busy')
        clock[0]=701
        with self.assertRaises(c.CaptureError) as found: await service.capture(cursor=cursor)
        self.assertEqual(found.exception.reason,'snapshot_expired')
        service,_=self.setup_capture(fixture(0)); baseline,page=await export(service)
        self.validate(baseline); self.assertEqual(baseline['inventory']['completeness'],'complete')
        self.assertEqual(page['pagination']['returned'],0)

    async def test_cancel_stops_config_collection_and_drains(self):
        service,client=self.setup_capture(fixture(20))
        entered=asyncio.Event()
        async def hook(kind):
            if kind=='config':
                entered.set(); await asyncio.Event().wait()
        client.hook=hook
        task=asyncio.create_task(service.capture())
        await asyncio.wait_for(entered.wait(),3)
        with self.assertRaises(c.CaptureError) as found: await service.capture()
        self.assertEqual(found.exception.reason,'capacity_busy')
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        calls=len(client.calls); await asyncio.sleep(.01)
        self.assertEqual(calls,len(client.calls)); self.assertEqual(client.active,0)
        self.assertFalse(service.active); self.assertEqual(service.snapshots,{})
        self.assertTrue(client.closed)

    def test_maximum_capture_bounded_pages_responsive(self):
        # As in the Alarmo responsiveness check, isolate natural GC from the
        # unrelated heap retained by full discovery. Keep the wall bound and
        # additionally measure loop-thread CPU; never subtract GC or retry.
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-c',
             'import sys, unittest; sys.path.insert(0, sys.argv.pop(1)); '
             'unittest.main(module=None)',
             str(Path(__file__).resolve().parent),
             'test_automation_baseline_capture.CaptureTests.'
             '_maximum_capture_bounded_pages_responsive', '-v'],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
        print(result.stdout, end='', flush=True)
        for line in result.stderr.splitlines():
            if line.startswith('Ran '):
                line = 'Isolated child test summary: ' + line.removeprefix('Ran ')
            print(line, file=sys.stderr, flush=True)
        self.assertEqual(result.returncode, 0, 'Isolated baseline responsiveness check failed.')

    def test_maximum_capture_child_failure_is_not_accepted(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        failed = subprocess.CompletedProcess([], 1, 'synthetic failure output\n', 'synthetic failure detail\n')
        with patch('test_automation_baseline_capture.subprocess.run', return_value=failed) as child:
            with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
                with self.assertRaisesRegex(AssertionError, 'Isolated baseline responsiveness check failed'):
                    self.test_maximum_capture_bounded_pages_responsive()
            self.assertEqual(out.getvalue(), failed.stdout)
            self.assertEqual(err.getvalue(), failed.stderr)
            self.assertEqual(child.call_count, 1)

    def test_maximum_capture_child_summary_preserves_discovery_count(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        passed = subprocess.CompletedProcess([], 0, '{"fixture": "synthetic"}\n', 'Ran 1 test in 0.001s\n\nOK\n')
        with patch('test_automation_baseline_capture.subprocess.run', return_value=passed):
            with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
                self.test_maximum_capture_bounded_pages_responsive()
            self.assertEqual(out.getvalue(), passed.stdout)
            self.assertIn('Isolated child test summary: 1 test in 0.001s', err.getvalue())
            self.assertNotRegex(out.getvalue() + err.getvalue(), r'Ran\s+(\d+)\s+tests?')

    def test_maximum_capture_child_timeout_is_not_retried(self):
        with patch('test_automation_baseline_capture.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('synthetic child', 30)) as child:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.test_maximum_capture_bounded_pages_responsive()
            self.assertEqual(child.call_count, 1)

    async def _maximum_capture_bounded_pages_responsive(self):
        import gc
        import threading

        service,client=self.setup_capture(fixture(1001))
        gaps, cpu_gaps = [], []
        async def heartbeat():
            last, last_cpu = time.perf_counter(), time.thread_time()
            while True:
                await asyncio.sleep(.005)
                current, current_cpu = time.perf_counter(), time.thread_time()
                gaps.append(current-last)
                cpu_gaps.append(current_cpu-last_cpu)
                last, last_cpu = current, current_cpu
        # Diagnostic only. Collections may run on a worker: attribute their
        # thread instead of presenting worker CPU as event-loop CPU.
        loop_thread = threading.get_ident()
        gc_events, gc_started = [], {}
        gc_state = {'enabled': gc.isenabled(), 'thresholds': gc.get_threshold()}
        def observe_gc(phase, info):
            if info['generation'] != 2:
                return
            thread = threading.get_ident()
            if phase == 'start':
                gc_started[thread] = (time.perf_counter(), time.thread_time())
            elif thread in gc_started:
                wall, cpu = gc_started.pop(thread)
                if len(gc_events) < 64:
                    gc_events.append({'generation': 2, 'event_loop_thread': thread == loop_thread,
                                      'wall_seconds': time.perf_counter()-wall,
                                      'thread_cpu_seconds': time.thread_time()-cpu,
                                      'collected': info['collected'], 'uncollectable': info['uncollectable']})
        pulse=asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        gc.callbacks.append(observe_gc)
        try:
            baseline,page=await export(service,100)
            await asyncio.sleep(.01)
        finally:
            gc.callbacks.remove(observe_gc)
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
        print(json.dumps({'fixture': 'maximum_automation_baseline_inventory',
                          'maximum_loop_gap_seconds': max(gaps),
                          'maximum_thread_cpu_gap_seconds': max(cpu_gaps),
                          'wall_bound_seconds': .25, 'thread_cpu_bound_seconds': .1,
                          'records': len(baseline['records']), 'gc_state': gc_state,
                          'major_gc_events': gc_events}), flush=True)
        self.assertLess(max(gaps), .25, 'Capture and export must keep the event loop responsive under contention.')
        self.assertLess(max(cpu_gaps), .1, 'Capture and export must yield within the loop-thread CPU budget.')
        self.validate(baseline)
        self.assertEqual(len(baseline['records']),1000)
        self.assertEqual(baseline['inventory']['omitted_count'],1)
        self.assertTrue(baseline['inventory']['limit_reached'])
        self.assertEqual(client.requests,1010)
        self.assertLessEqual(len(c.canonical(page)),c.PAGE_BYTES)

    async def test_deadline_partial_no_new_reads(self):
        service,client=self.setup_capture()
        with patch.object(c,'COLLECTION_SECONDS',10):
            baseline,page=await export(service)
        self.validate(baseline)
        self.assertEqual(client.requests,10)
        self.assertEqual(page['diagnostics']['configuration_incomplete_count'],3)
        self.assertTrue(all(r['configuration']['status']=='omitted' for r in baseline['records']))

    async def test_dispatch_requires_admission_and_request_authority(self):
        for callback in (None,lambda:False):
            service,client=self.setup_capture()
            self.telemetry.core_dispatch_authorizer=callback
            with self.assertRaises(c.CaptureError): await service.capture()
            self.assertEqual(client.calls,[])

class ContractTests(unittest.TestCase):
    def test_arguments_are_closed_and_diagnostics_fixed(self):
        for args in ({'limit':True},{'limit':101},{'cursor':'SECRET'}, {'endpoint':SECRET},[] ):
            with self.assertRaises(c.CaptureError) as found: c.validate_arguments(args)
            self.assertEqual(found.exception.reason,'invalid_arguments')
            self.assertNotIn(SECRET,str(found.exception))
    def test_parse_duplicate_nonfinite_depth_and_wide_bounds(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}', b'['*66+b'0'+b']'*66):
            with self.assertRaises(c.CaptureError): c.parse(raw)
        with patch.object(c,'MAX_NODES',10):
            with self.assertRaises(c.CaptureError): c.parse(b'['+b'0,'*10+b'0]')
    def test_identifier_sanitization_and_no_arbitrary_recursion(self):
        projector=Projector((SECRET,))
        for value in (SECRET,'..','x/y','x?query','http://private','ignore instructions'):
            self.assertIsNone(projector.identifier(value))
        self.assertEqual(projector.identifier('valid-id'),'valid-id')

class PublicBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_invalid_arguments_never_echo_and_catalog_route_is_read_only(self):
        from ha_mcp_engineering.tools import get_registered_server,registered_tools
        from ha_mcp_engineering.providers.routing import routing_for_tool
        from ha_mcp_engineering.ha_core_readmission.routes import static_tool_requirements
        tools=registered_tools(get_registered_server()); tool=tools['capture_automation_baseline']
        self.assertEqual(len(tools),57)
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertFalse(tool.parameters['additionalProperties'])
        self.assertEqual(routing_for_tool('capture_automation_baseline').fallback_providers,())
        self.assertEqual(set(static_tool_requirements('capture_automation_baseline',{})),set(c.REQUIREMENTS))
        for args in ({'PRIVATE_KEY':SECRET},{'limit':True},{'cursor':SECRET},None):
            response=await tool.run(args)
            self.assertFalse(json.loads(response)['success'])
            self.assertNotIn(SECRET,response); self.assertNotIn('PRIVATE_KEY',response)

    async def test_gateway_invalid_arguments_not_echoed_or_audited(self):
        import httpx
        from unittest.mock import Mock
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.configuration import Settings
        with tempfile.TemporaryDirectory() as directory:
            audit=Path(directory)/'audit.jsonl'
            settings=Settings(ha_url='http://synthetic.invalid',ha_token='SYNTHETIC_HA_TOKEN',access_secret='SYNTHETIC_ACCESS_TOKEN',port=8100,audit_path=str(audit),rate_limit_per_minute=1000,rate_limit_burst=1000,destructive_services=frozenset())
            app=Mock(side_effect=AssertionError('SDK must not run'));core=SimpleNamespace(acquire=Mock())
            gateway=AuthenticatedMcpGateway(app,settings,AuditLogger(str(audit),settings.access_secret),core_runtime=core)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),base_url='http://127.0.0.1:8100') as client:
                response=await client.post('/SYNTHETIC_ACCESS_TOKEN/mcp',json={'jsonrpc':'2.0','id':'synthetic-request','method':'tools/call','params':{'name':'capture_automation_baseline','arguments':{'PRIVATE_KEY':SECRET,'cursor':SECRET}}})
            self.assertIn('invalid_request',response.text)
            self.assertNotIn(SECRET,response.text+audit.read_text())
            self.assertNotIn('PRIVATE_KEY',audit.read_text())
            core.acquire.assert_not_called(); app.assert_not_called()

    async def test_signed_candidate_admits_only_exact_new_profile(self):
        from core_registry_fixtures import CoreSigner,ProjectedCoreSource,core_entry
        from signed_registry_fixtures import NOW
        from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
        from ha_mcp_engineering.ha_core_readmission.profiles import CORE_AUTOMATION_BASELINE_PROFILES
        from ha_mcp_engineering.ha_core_readmission import CoreRuntime
        profile=CORE_AUTOMATION_BASELINE_PROFILES[0]
        class Source(ProjectedCoreSource):
            async def capture_core_snapshot(self):
                value=await super().capture_core_snapshot()
                value['capability_evidence'].append({'capability_id':profile.capability_id,'passed_checks':list(profile.required_checks),'semantic_fingerprint':profile.contract_fingerprint})
                return value
        for case in ('absent','changed','exact'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                signer=CoreSigner();entry=core_entry('2026.9.3',typed_operations=True)
                if case!='absent':
                    reference={k:profile.to_mapping()[k] for k in ('capability_id','profile_id','profile_version','adapter_id','contract_fingerprint')}
                    if case=='changed':reference['contract_fingerprint']='sha256:'+'e'*64
                    entry['capabilities'].append(reference)
                signed=signer.journal_raw(envelopes=[signer.raw(entries=[entry])])
                async def fetch(*_):return signed
                registry=CoreReleaseRegistry(enabled=True,public_key=signer.public_key_base64,cache_path=Path(directory)/'registry.json',fetcher=fetch,now=lambda:NOW)
                self.assertTrue(await registry.refresh())
                runtime=CoreRuntime();runtime.configure(SimpleNamespace(),source=Source(registry,'2026.9.3',typed_operations=True),release_registry=registry)
                await runtime.reconcile_once('synthetic-baseline')
                lease=runtime.acquire(c.REQUIREMENTS)
                self.assertEqual(lease is not None,case=='exact')
                commits=runtime.consume(lease) if lease else None
                telemetry,token=begin_request();telemetry.caller_id='synthetic-signed-capture'
                telemetry.core_dispatch_authorizer=lambda:bool(lease and runtime.revalidate(lease,commits))
                try:
                    client=Client(fixture());service=BaselineCaptureService(BaselineCaptureProvider(client,runtime))
                    if case=='exact':
                        baseline,_=await export(service);self.assertEqual(len(baseline['records']),3)
                    else:
                        with self.assertRaises(c.CaptureError):await service.capture()
                        self.assertEqual(client.calls,[])
                finally:
                    end_request(token)
                    if commits:self.assertTrue(runtime.finish(commits))
                self.assertEqual(runtime.health_snapshot()['active_commit_count'],0)

    async def test_readiness_is_opt_in_and_requires_existing_rest_and_websocket(self):
        from ha_mcp_engineering.ha_core_readmission.source import capability_evidence_for_probes
        from ha_mcp_engineering.ha_core_readmission.probe_profiles import CHILD_DEVICE_PROBE_PROFILE
        args=dict(version='2026.9.4',rest_config={'version':'2026.9.4'},states=[],services=[],websocket_config={'version':'2026.9.4'},websocket_results={},probe_profile=CHILD_DEVICE_PROBE_PROFILE)
        old=capability_evidence_for_probes(**args)
        new=capability_evidence_for_probes(**args,include_automation_baseline=True)
        self.assertEqual([x for x in new if x['capability_id']!=c.CAPABILITY],old)
        self.assertEqual(len(new),len(old)+1)
        for change in ({'probe_profile':None},{'rest_config':{}},{'websocket_config':{}}):
            result=capability_evidence_for_probes(**{**args,**change},include_automation_baseline=True)
            self.assertNotIn(c.CAPABILITY,{x['capability_id'] for x in result})

    async def test_public_success_pages_keep_exact_baseline_digest(self):
        from ha_mcp_engineering.audit_baseline.capture_runtime import AUTOMATION_BASELINE_CAPTURE
        from ha_mcp_engineering.tools.audit_baseline import registered_tool
        telemetry,token=begin_request();telemetry.caller_id='synthetic-public';telemetry.core_dispatch_authorizer=lambda:True
        client=Client(fixture(101));service=BaselineCaptureService(BaselineCaptureProvider(client,Core()))
        try:
            with patch.object(AUTOMATION_BASELINE_CAPTURE,'service',service):
                tool=registered_tool(); response=json.loads(await tool.run({'limit':100}))
                self.assertTrue(response['success'],response)
                page=response['data'];baseline=copy.deepcopy(page['baseline_header']);baseline['records']=page['records']
                self.assertEqual(response['metadata']['completeness'],'complete')
                while page['pagination']['next_cursor']:
                    response=json.loads(await tool.run({'limit':100,'cursor':page['pagination']['next_cursor']}))
                    self.assertTrue(response['success'],response)
                    page=response['data'];baseline['records'].extend(page['records'])
                self.assertEqual(len(baseline['records']),101)
                self.assertEqual(c.digest(baseline),page['artifact_sha256'])
                self.assertEqual(client.requests,111)
        finally:end_request(token)
