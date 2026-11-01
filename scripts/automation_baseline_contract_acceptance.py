"""Assembled exact-Core baseline read lane with owned synthetic fixture setup.

Called only by the disposable Alarmo-slot harness (HAMCP-159: every non-empty
support-window Core slot). No standalone/live CLI.
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
EXPECTED_COMMANDS = ["auth/current_user", "config_entries/get",
                     "config/device_registry/list", "config/entity_registry/list"] * 2


class ComparisonProofError(AssertionError):
    def __init__(self, suffix, expected, record):
        super().__init__("Synthetic baseline comparison proof failed")
        self.diagnostic = {"fixture_suffix": suffix, "expected": expected,
                           "observed": record.classification.value if record else None,
                           "reason_codes": list(record.reasons) if record else []}


def verify_predecessor(response, requests, acquired):
    """A real acquisition refusal, not a manually disabled dispatch context."""
    assert acquired is False and type(requests) is int and requests == 0
    assert response['success'] is False and response['details']['reason'] == 'authority_unavailable'
    return {'acquired': acquired, 'ha_requests': requests, 'reason': 'authority_unavailable'}


def verify_result(result, *, core_version, support_slot):
    """Offline receipt reconstruction; does not substitute for execution.

    The receipt must name the exact Core and the exact support-window slot the
    caller bound that Core to (HAMCP-159); a missing or other slot refuses.
    """
    from ha_mcp_engineering.audit_baseline import load_baseline, compare_baselines
    from ha_mcp_engineering.audit_baseline.capture_contracts import digest, canonical
    from alarmo_inspection_contract_acceptance import verify_discarded
    assert type(core_version) is str and result['result'] == 'PASS' and result['core'] == core_version
    assert type(support_slot) is str and type(result.get('support_slot')) is str
    assert result['support_slot'] == support_slot
    assert result['production_authority'] is False and result['fixture_cleanup'] is True
    assert result['authority_transition'] == [20, 21]
    assert result['continuation_reads'] == 0 and result['nonadmin_refusal'] == 'access_denied'
    assert result['missing_authority_reads'] == 0 and result['recreated_anchor_differs'] is True
    assert result['predecessor_admission'] == {
        'acquired': False, 'ha_requests': 0, 'reason': 'authority_unavailable'}
    assert result['predecessor_admission']['acquired'] is False
    assert type(result['predecessor_admission']['ha_requests']) is int
    assert result['restored_anchor_preserved'] is True
    values = result['baselines']
    assert len(values) == 2 and [digest(b) for b in values] == result['baseline_sha256']
    with tempfile.TemporaryDirectory(prefix='native-baseline-receipt-') as directory:
        paths = [Path(directory)/str(i) for i in range(2)]
        for path, value in zip(paths, values): path.write_bytes(canonical(value))
        report = compare_baselines(*(load_baseline(p) for p in paths))
    actual = {r.configuration_id: r for r in report.records}
    for key, status in {'000':'CHANGED','001':'REMOVED','002':'UNCHANGED','121':'ADDED','unknown':'UNKNOWN'}.items():
        record = actual.get(PREFIX + key)
        if record is None or record.classification.value != status:
            raise ComparisonProofError(key, status, record)
    assert report.counts == result['comparison_counts']
    assert len(result['observations']) == 2
    proofs = [verify_export(observation, index, core_version=core_version)
              for index, observation in enumerate(result['observations'], start=1)]
    # HAMCP-219 review 1: a discarded export window is retained and must be
    # retryable and nothing else, at most once per export.
    discarded = verify_discarded(result['discarded_observations'], lambda window, observation, retry:
        verify_export(observation, EXPORT_WINDOWS[window], core_version=core_version, retry_allowed=retry),
        EXPORT_WINDOWS)
    return {'result': 'PASS', 'records_before': len(values[0]['records']),
            'records_after': len(values[1]['records']), 'comparison_counts': report.counts,
            'continuation_reads': 0, 'fixture_cleanup': True,
            'background_bookkeeping': [proof['background_bookkeeping'] for proof in proofs],
            'discarded_windows': discarded}


# Discarded-window labels for the two exports (HAMCP-219 review 1).
EXPORT_WINDOWS = {'baseline_export_1': 1, 'baseline_export_2': 2}


def verify_export(observation, index, *, core_version, retry_allowed=False):
    from alarmo_inspection_contract_acceptance import verify_interval
    return verify_interval(observation, observation['interval_id'], EXPECTED_COMMANDS, kind='control',
        core_version=core_version, window='baseline_export', window_index=index, retry_allowed=retry_allowed)


def screen_export(observation, index, *, core_version, retry_allowed):
    """Only decide whether an export window is rerun; never accept it here.

    A retryable window raises; any other refusal is left to ``verify_result``
    after fixture cleanup, so the sanitized exports are still retained.
    """
    from alarmo_inspection_contract_acceptance import ObservationError, RetryableObservation
    try:
        verify_export(observation, index, core_version=core_version, retry_allowed=retry_allowed)
    except RetryableObservation:
        raise
    except ObservationError:
        pass


async def run_disposable(configured, *, expected_image, core_version):
    """Preserve a bounded source location on failure, never exception values."""
    try:
        return await _run_disposable(configured, expected_image=expected_image, core_version=core_version)
    except Exception as exc:
        cursor = exc.__traceback__
        lines = []
        while cursor is not None:
            if cursor.tb_frame.f_code.co_filename == __file__:
                lines.append(cursor.tb_lineno)
            cursor = cursor.tb_next
        exc.contract_missing_key = "automation_baseline"
        exc.contract_diagnostic = {"baseline_source_lines": lines[:12]}
        if isinstance(exc, ComparisonProofError):
            exc.contract_diagnostic["comparison_failure"] = exc.diagnostic
        from alarmo_inspection_contract_acceptance import ObservationError
        if isinstance(exc, ObservationError):
            # HAMCP-180: which interval or settle condition failed, bounded tokens only.
            exc.contract_diagnostic["observation_failure"] = exc.diagnostic
        raise


def retain_failed_result(receipt_path, result, failure):
    """Keep sanitized synthetic exports beside the failed proof in the Alarmo result.

    The retained result is parsed through the shared strict loader (HAMCP-180),
    so a repeated key refuses before anything is rewritten.
    """
    from alarmo_inspection_contract_acceptance import ObservationError
    from entity_metadata_contract_acceptance import load_receipt
    retained = load_receipt(Path(receipt_path).read_bytes())
    failed = {**result, 'result': 'FAIL'}
    if isinstance(failure, ObservationError):
        failed['observation_failure'] = failure.diagnostic
    retained['automation_baseline'] = failed
    Path(receipt_path).write_text(json.dumps(retained, sort_keys=True, indent=2) + '\n')


async def _run_disposable(configured, *, expected_image, core_version):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from core_registry_contract_lane import lane_entry, probe_profile_for
    from prepare_core_release_registry import prepare_candidate, sign_candidate
    from alarmo_inspection_contract_acceptance import (slot_binding, wait_disposable_setup, start_interval,
        close_disposable_authority, observe_retry_once_clean)
    from entity_metadata_contract_acceptance import load_receipt
    from ha_mcp_engineering.clients import HomeAssistantWebSocketClient
    from ha_mcp_engineering.clients.audit_baseline import BaselineReadClient
    from ha_mcp_engineering.audit_baseline.capture_provider import BaselineCaptureProvider
    from ha_mcp_engineering.audit_baseline.capture_service import BaselineCaptureService
    from ha_mcp_engineering.audit_baseline.store import BaselineStore
    from ha_mcp_engineering.audit_baseline.capture_runtime import AUTOMATION_BASELINE_CAPTURE
    from ha_mcp_engineering.audit_baseline import capture_contracts as c
    from ha_mcp_engineering.tools.audit_baseline import registered_tool
    from ha_mcp_engineering.request_context import begin_request, end_request
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
    from ha_mcp_engineering.ha_core_readmission.profiles import CORE_RUNTIME_CAPABILITY_PROFILES
    from ha_mcp_engineering.signed_registry import canonical_json

    binding = slot_binding(core_version)
    entry = lane_entry(core_version)
    if (urlsplit(configured.ha_url).hostname not in {'127.0.0.1','localhost','::1'}
            or os.environ.get('HA_CONTRACT_LANE') != binding['lane']
            or expected_image != binding['image']
            or entry['image_index_digest'] != binding['image_index_digest']):
        raise ValueError('Exact owned disposable baseline lane required')
    probe_profile = probe_profile_for(core_version)
    image = load_receipt(Path(os.environ['REAL_HA_ALARMO_IMAGE_RESULT']).read_bytes())
    assert (image['result']=='PASS' and image['index']==entry['image_index_digest']
            and image['core_version']==core_version and image['support_slot']==binding['slot'])
    refs = [{k:p.to_mapping()[k] for k in ('capability_id','profile_id','profile_version','adapter_id','contract_fingerprint')}
            for p in CORE_RUNTIME_CAPABILITY_PROFILES
            # HAMCP-069A/069B/155/165/232/145: the dormant entity, delete, label and November-batch
            # (seq 15+) profiles are not signed here.
            if p.capability_id not in {'core.entity_registry_metadata_operation',
                                       'core.entity_registry_availability_operation',
                                       'core.entity_assistant_exposure_operation',
                                       'core.configuration_delete_operation',
                                       'core.label_registry_operation',
                                       'core.config_entry_helper_options_operation',
                                       'core.typed_input_select_option_operation',
                                       'core.script_trace_read',
                                       'core.dashboard_metadata_operation',
                                       'core.scene_configuration_operation'}]
    old = [r for r in refs if r['capability_id'] != c.CAPABILITY]
    assert len(old)==20 and len(refs)==21
    key=Ed25519PrivateKey.generate(); now=datetime.now(timezone.utc)
    evidence=b'Synthetic assembled Core baseline lane; no production applicability'
    entry.update(evidence_sha256=hashlib.sha256(evidence).hexdigest(),
        probe_profile_id=probe_profile.profile_id,
        probe_profile_sha256=probe_profile.contract_fingerprint)
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
            await runtime.reconcile_once('startup')
            assert runtime.health_snapshot()['compatible_count']==len(references)
            return runtime
        # HAMCP-228: one disposable durable store for the whole run, under
        # this run's temporary directory (the runtime uses governance_path).
        store=BaselineStore(Path(directory)/'governance')
        def service(runtime, options=configured):
            return BaselineCaptureService(BaselineCaptureProvider(BaselineReadClient(options),runtime,
                known_secrets=(options.ha_token,options.access_secret)),store)
        async def request(runtime, capture, arguments):
            telemetry, token=begin_request();telemetry.caller_id='synthetic-native-baseline-owner'
            lease=runtime.acquire(c.REQUIREMENTS)
            commits=runtime.consume(lease) if lease else None
            telemetry.core_dispatch_authorizer=lambda: bool(lease and runtime.revalidate(lease,commits))
            try:
                with patch.object(AUTOMATION_BASELINE_CAPTURE,'service',capture):
                    answer=json.loads(await registered_tool().run(arguments))
                return answer,telemetry.ha_request_count,lease is not None
            finally:
                end_request(token)
                if commits: assert runtime.finish(commits)
        discarded=[]
        async def export(runtime,capture,index):
            # HAMCP-219 review 1: rerun once only for a repairs delayed save.
            async def window():
                return await export_once(runtime,capture,index)
            def screen(outcome,observed,retry):
                screen_export(observed,index,core_version=core_version,retry_allowed=retry)
            (baseline,artifact),observed,_,_=await observe_retry_once_clean(window,screen,
                on_discard=lambda item: discarded.append({'window':'baseline_export_'+str(index),**item}))
            return baseline,artifact,observed
        async def export_once(runtime,capture,index):
            interval=await start_interval(websocket,'control',window='baseline_export',window_index=index)
            first, reads,_=await request(runtime,capture,{'limit':17})
            assert first['success'], 'Baseline public call refused'
            page=first['data'];baseline={**page['baseline_header'],'records':list(page['records'])}
            artifact=page['artifact_sha256'];continuation_reads=0
            assert page['snapshot']['storage']=='durable_engineering_store'
            while page['pagination']['next_cursor']:
                result, count,_=await request(runtime,capture,{'limit':17,'cursor':page['pagination']['next_cursor']})
                assert result['success']; page=result['data'];continuation_reads+=count
                assert page['artifact_sha256']==artifact and page['pagination']['offset']==len(baseline['records'])
                assert page['baseline_header']=={k:v for k,v in baseline.items() if k!='records'}
                baseline['records'].extend(page['records'])
            observed=await websocket.command({'type':'alarmo_interval_observer/finish','interval_id':interval})
            assert c.digest(baseline)==artifact and continuation_reads==0
            assert reads==len(baseline['records'])+11
            assert baseline['installation']['status']=='established'
            return (baseline,artifact),observed
        seeded=False
        try:
            predecessor=await authority(old,'old')
            predecessor_admission=verify_predecessor(
                *await request(predecessor,service(predecessor),{}))
            runtime=await authority(refs,'new')
            # A plain container has no supervised registry anchor: no fallback.
            missing,_,_=await request(runtime,service(runtime),{})
            assert not missing['success'] and missing['details']['reason']=='identity_unverified'
            seeded=True
            seed=await fixture('seed'); assert seed['fixture']=='native-baseline-v1'
            nonadmin=replace(configured, ha_token=seed.pop('nonadmin_token'))
            refused,_,_=await request(runtime,service(runtime,nonadmin),{})
            assert not refused['success'] and refused['details']['reason']=='access_denied'
            await wait_disposable_setup(websocket,window='baseline_export',window_index=1)
            capture=service(runtime)
            before,before_hash,obs1=await export(runtime,capture,1)
            native={r['configuration_id']:r for r in before['records'] if r['configuration_id'].startswith(PREFIX)}
            assert len(native)==122 and native[PREFIX+'000']['enabled_state']['state']=='off'
            assert native[PREFIX+'unknown']['configuration']['status']=='unreadable'
            assert before['inventory']['completeness']=='complete'
            await fixture('change'); await wait_disposable_setup(websocket,window='baseline_export',window_index=2)
            after,after_hash,obs2=await export(runtime,capture,2)
            # Run the unchanged public CLI, not a test-only comparison function.
            a,b=Path(directory)/'before.json',Path(directory)/'after.json'
            a.write_bytes(c.canonical(before));b.write_bytes(c.canonical(after))
            import asyncio
            process=await asyncio.to_thread(subprocess.run,
                [sys.executable,'-B',str(ROOT/'scripts/compare_automation_baselines.py'),str(a),str(b)],
                capture_output=True,timeout=30,check=True)
            compared=json.loads(process.stdout)
            await fixture('restore');await wait_disposable_setup(websocket,window='baseline_restore')
            restored,_,_=await request(runtime,service(runtime),{'limit':1})
            assert restored['success']
            assert restored['data']['baseline_header']['installation']['installation_id'] == before['installation']['installation_id']
            await fixture('recreate');await wait_disposable_setup(websocket,window='baseline_recreate')
            recreated,_,_=await request(runtime,service(runtime),{'limit':1})
            assert recreated['success']
            assert recreated['data']['baseline_header']['installation']['installation_id'] != before['installation']['installation_id']
            result={'result':'PASS','core':core_version,'support_slot':binding['slot'],'authority_transition':[20,21],
                'authority_instances':'separate_ephemeral_registries','production_authority':False,
                'image_receipt_sha256':hashlib.sha256(Path(os.environ['REAL_HA_ALARMO_IMAGE_RESULT']).read_bytes()).hexdigest(),
                'synthetic_hassio_metadata':True,'supervisor_setup_proven':False,
                'missing_anchor_refusal':'identity_unverified','nonadmin_refusal':'access_denied',
                'missing_authority_reads':predecessor_admission['ha_requests'],
                'predecessor_admission':predecessor_admission,
                'recreated_anchor_differs':True,'restored_anchor_preserved':True,'continuation_reads':0,
                'baselines':[before,after],'baseline_sha256':[before_hash,after_hash],
                'comparison_counts':compared['counts'],'observations':[obs1,obs2],
                'discarded_observations':discarded,'fixture_cleanup':False}
        finally:
            try:
                if seeded:
                    cleanup=await fixture('cleanup');assert cleanup=={'result':'PASS','action':'cleanup'}
            finally:
                for runtime in runtimes: await close_disposable_authority(runtime)
        result['fixture_cleanup']=True
        try:
            summary=verify_result(result, core_version=core_version, support_slot=binding['slot'])
        except Exception as failure:
            # Retain sanitized synthetic exports even when their final proof
            # fails; outer CI cleanup must continue to reject this receipt.
            retain_failed_result(os.environ['REAL_HA_ALARMO_RESULT'], result, failure)
            raise
        print('Native automation baseline disposable contract: '+json.dumps(summary,sort_keys=True))
        return result
