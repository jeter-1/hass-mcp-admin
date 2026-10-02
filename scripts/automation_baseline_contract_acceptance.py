"""Assembled exact-Core baseline read lane with owned synthetic fixture setup.

Called only by the existing disposable .4 harness. No standalone/live CLI.
Source/schema/public-tool code is the candidate itself; setup is outside reads.
"""
import base64
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
PREFIX = "native_baseline_"
CONTROL = "beta23_device_fixture/baseline"


def verify_result(result):
    """Offline receipt reconstruction; does not substitute for execution."""
    from ha_mcp_engineering.audit_baseline import load_baseline, compare_baselines
    from ha_mcp_engineering.audit_baseline.capture_contracts import digest, canonical
    from alarmo_inspection_contract_acceptance import verify_interval
    assert result['result'] == 'PASS' and result['core'] == '2026.9.4'
    assert result['production_authority'] is False and result['fixture_cleanup'] is True
    assert result['authority_transition'] == [20, 21]
    assert result['continuation_reads'] == 0 and result['nonadmin_refusal'] == 'access_denied'
    assert result['missing_authority_reads'] == 0 and result['recreated_anchor_differs'] is True
    values = result['baselines']
    assert len(values) == 2 and [digest(b) for b in values] == result['baseline_sha256']
    with tempfile.TemporaryDirectory(prefix='native-baseline-receipt-') as directory:
        paths = [Path(directory)/str(i) for i in range(2)]
        for path, value in zip(paths, values): path.write_bytes(canonical(value))
        report = compare_baselines(*(load_baseline(p) for p in paths))
    actual = {r.configuration_id: r.classification.value for r in report.records}
    for key, status in {'000':'CHANGED','001':'REMOVED','002':'UNCHANGED','121':'ADDED','unknown':'UNKNOWN'}.items():
        assert actual[PREFIX + key] == status
    assert report.counts == result['comparison_counts']
    assert len(result['observations']) == 2
    for observation in result['observations']:
        verify_interval(observation, observation['interval_id'],
            ['other', 'config_entries/get', 'other', 'other'] * 2, kind='control')
    return {'result': 'PASS', 'records_before': len(values[0]['records']),
            'records_after': len(values[1]['records']), 'comparison_counts': report.counts,
            'continuation_reads': 0, 'fixture_cleanup': True}


async def run_disposable(configured, *, expected_image):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from core_registry_contract_lane import lane_entry
    from prepare_core_release_registry import prepare_candidate, sign_candidate
    from alarmo_inspection_contract_acceptance import wait_disposable_setup, close_disposable_authority
    from ha_mcp_engineering.clients import HomeAssistantWebSocketClient
    from ha_mcp_engineering.clients.audit_baseline import BaselineReadClient
    from ha_mcp_engineering.audit_baseline.capture_provider import BaselineCaptureProvider
    from ha_mcp_engineering.audit_baseline.capture_service import BaselineCaptureService
    from ha_mcp_engineering.audit_baseline.capture_runtime import AUTOMATION_BASELINE_CAPTURE
    from ha_mcp_engineering.audit_baseline import capture_contracts as c
    from ha_mcp_engineering.tools.audit_baseline import registered_tool
    from ha_mcp_engineering.request_context import begin_request, end_request
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    from ha_mcp_engineering.ha_core_readmission.probe_profiles import CHILD_DEVICE_PROBE_PROFILE
    from ha_mcp_engineering.signed_registry import canonical_json

    entry = lane_entry('2026.9.4')
    if (urlsplit(configured.ha_url).hostname not in {'127.0.0.1','localhost','::1'}
            or os.environ.get('HA_CONTRACT_LANE') != 'ha-2026-9-4'
            or expected_image != 'ghcr.io/home-assistant/home-assistant:2026.9.4@'+entry['image_index_digest']):
        raise ValueError('Exact owned disposable baseline lane required')
    image = json.loads(Path(os.environ['REAL_HA_ALARMO_IMAGE_RESULT']).read_text())
    assert image['result']=='PASS' and image['index']==entry['image_index_digest']
    refs = [{k:p.to_mapping()[k] for k in ('capability_id','profile_id','profile_version','adapter_id','contract_fingerprint')}
            for p in CORE_RUNTIME_CAPABILITY_PROFILES]
    old = [r for r in refs if r['capability_id'] != c.CAPABILITY]
    assert len(old)==20 and len(refs)==21
    key=Ed25519PrivateKey.generate(); now=datetime.now(timezone.utc)
    evidence=b'Synthetic assembled Core baseline lane; no production applicability'
    entry.update(evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        probe_profile_id=CHILD_DEVICE_PROBE_PROFILE.profile_id,
        probe_profile_sha256=CHILD_DEVICE_PROBE_PROFILE.contract_fingerprint)
    def signed(references):
        candidate=canonical_json(prepare_candidate(entry={**entry,'capabilities':references}, evidence=evidence,
            previous=None,public_key=key.public_key(),now=now))
        return sign_candidate(candidate,expected_sha256=hashlib.sha256(candidate).hexdigest(),previous=None,key=key,now=now)
    websocket=HomeAssistantWebSocketClient(configured)
    async def fixture(action): return await websocket.command({'type':CONTROL,'action':action})
    runtimes=[]
    with tempfile.TemporaryDirectory(prefix='native-baseline-core-') as directory:
        async def authority(references, name):
            payload=signed(references)
            async def fetch(_url,_maximum): return payload
            registry=CoreReleaseRegistry(enabled=True,public_key=base64.b64encode(key.public_key().public_bytes_raw()).decode(),
                fetcher=fetch,cache_path=Path(directory)/(name+'.json'))
            assert await registry.refresh()
            runtime=CoreRuntime();runtimes.append(runtime)
            runtime.configure(configured,release_registry=registry)
            await runtime.reconcile_once('native_baseline_disposable')
            assert runtime.health_snapshot()['compatible_count']==len(references)
            return runtime
        def service(runtime, options=configured):
            return BaselineCaptureService(BaselineCaptureProvider(BaselineReadClient(options),runtime,
                known_secrets=(options.ha_token,options.access_secret)))
        async def request(runtime, capture, arguments, *, allowed=True):
            telemetry, token=begin_request();telemetry.caller_id='synthetic-native-baseline-owner'
            lease=runtime.acquire(c.REQUIREMENTS) if allowed else None
            commits=runtime.consume(lease) if lease else None
            telemetry.core_dispatch_authorizer=lambda: bool(lease and runtime.revalidate(lease,commits))
            try:
                with patch.object(AUTOMATION_BASELINE_CAPTURE,'service',capture):
                    answer=json.loads(await registered_tool().run(arguments))
                return answer,telemetry.ha_request_count
            finally:
                end_request(token)
                if commits: assert runtime.finish(commits)
        async def export(runtime,capture):
            start=await websocket.command({'type':'alarmo_interval_observer/start','kind':'control'})
            first, reads=await request(runtime,capture,{'limit':17})
            assert first['success'], 'Baseline public call refused'
            page=first['data'];baseline={**page['baseline_header'],'records':list(page['records'])}
            artifact=page['artifact_sha256'];continuation_reads=0
            while page['pagination']['next_cursor']:
                result, count=await request(runtime,capture,{'limit':17,'cursor':page['pagination']['next_cursor']})
                assert result['success']; page=result['data'];continuation_reads+=count
                assert page['artifact_sha256']==artifact and page['pagination']['offset']==len(baseline['records'])
                assert page['baseline_header']=={k:v for k,v in baseline.items() if k!='records'}
                baseline['records'].extend(page['records'])
            observed=await websocket.command({'type':'alarmo_interval_observer/finish','interval_id':start['interval_id']})
            assert c.digest(baseline)==artifact and continuation_reads==0
            assert reads==len(baseline['records'])+11
            assert baseline['installation']['status']=='established'
            return baseline,artifact,observed
        seeded=False
        try:
            predecessor=await authority(old,'old')
            denied, count=await request(predecessor,service(predecessor),{},allowed=False)
            assert not denied['success'] and count==0
            runtime=await authority(refs,'new')
            # A plain container has no supervised registry anchor: no fallback.
            missing,_=await request(runtime,service(runtime),{})
            assert not missing['success'] and missing['details']['reason']=='identity_unverified'
            seeded=True
            seed=await fixture('seed'); assert seed['fixture']=='native-baseline-v1'
            nonadmin=replace(configured, ha_token=seed.pop('nonadmin_token'))
            refused,_=await request(runtime,service(runtime,nonadmin),{})
            assert not refused['success'] and refused['details']['reason']=='access_denied'
            await wait_disposable_setup(websocket)
            capture=service(runtime)
            before,before_hash,obs1=await export(runtime,capture)
            native={r['configuration_id']:r for r in before['records'] if r['configuration_id'].startswith(PREFIX)}
            assert len(native)==122 and native[PREFIX+'000']['enabled_state']['state']=='off'
            assert native[PREFIX+'unknown']['configuration']['status']=='unreadable'
            assert before['inventory']['completeness']=='complete'
            await fixture('change'); await wait_disposable_setup(websocket)
            after,after_hash,obs2=await export(runtime,capture)
            # Run the unchanged public CLI, not a test-only comparison function.
            a,b=Path(directory)/'before.json',Path(directory)/'after.json'
            a.write_bytes(c.canonical(before));b.write_bytes(c.canonical(after))
            import asyncio
            process=await asyncio.to_thread(subprocess.run,
                [sys.executable,'-B',str(ROOT/'scripts/compare_automation_baselines.py'),str(a),str(b)],
                capture_output=True,timeout=30,check=True)
            compared=json.loads(process.stdout)
            await fixture('recreate');await wait_disposable_setup(websocket)
            recreated,_=await request(runtime,service(runtime),{'limit':1})
            assert recreated['success']
            assert recreated['data']['baseline_header']['installation']['installation_id'] != before['installation']['installation_id']
            result={'result':'PASS','core':'2026.9.4','authority_transition':[20,21],
                'authority_instances':'separate_ephemeral_registries','production_authority':False,
                'image_receipt_sha256':hashlib.sha256(Path(os.environ['REAL_HA_ALARMO_IMAGE_RESULT']).read_bytes()).hexdigest(),
                'synthetic_hassio_metadata':True,'supervisor_setup_proven':False,
                'missing_anchor_refusal':'identity_unverified','nonadmin_refusal':'access_denied',
                'missing_authority_reads':0,'recreated_anchor_differs':True,'continuation_reads':0,
                'baselines':[before,after],'baseline_sha256':[before_hash,after_hash],
                'comparison_counts':compared['counts'],'observations':[obs1,obs2],'fixture_cleanup':False}
        finally:
            try:
                if seeded:
                    cleanup=await fixture('cleanup');assert cleanup=={'result':'PASS','action':'cleanup'}
            finally:
                for runtime in runtimes: await close_disposable_authority(runtime)
        result['fixture_cleanup']=True
        summary=verify_result(result)
        print('Native automation baseline disposable contract: '+json.dumps(summary,sort_keys=True))
        return result
