"""One exact beta.10/Core 2026.9.4 CI acceptance; no configurable remote target."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / 'tests/fixtures/core_logbook_beta10.json'
BRANCH = 'refs/heads/codex/beta10-core-logbook-integration'
LABEL = 'io.hass-mcp.logbook-acceptance'
MAX_BYTES = 2 * 1024 * 1024
FAILURE_REASONS = frozenset('''attribution_mismatch branch_event_mismatch checkout_mismatch
cleanup_not_verified cleanup_owner_mismatch cleanup_path_mismatch command_output_limit
container_image_mismatch core_fixture_exited core_identity_mismatch core_readiness_timeout
end_or_filter_mismatch entity_filter_mismatch evidence_limit fixture_cleanup_failed
github_runner_required image_config_mismatch index_digest_mismatch interval_or_entity_mismatch
job_mismatch local_command_failed manifest_mismatch message_entity_mismatch
missing_end_control_not_distinguishable negative_control_http_failure network_not_internal
platform_mismatch registered_tool_failure repository_mismatch request_count_mismatch
response_not_list retry_or_upstream_attempt run_identity_missing shipped_engineering_source_changed
source_missing start_mismatch telemetry_attempt_mismatch unexpected_dispatch unexpected_entity'''.split())


class RequirementFailure(ValueError):
    def __init__(self, reason):
        self.reason = reason if reason in FAILURE_REASONS else 'unclassified'
        super().__init__(self.reason)


def require(value, reason):
    if not value:
        raise RequirementFailure(reason)


def response_diagnostic(response):
    """Project fixed codes/counts and known synthetic labels; never raw text."""
    known_codes = {'authentication_failure','authorization_failure','home_assistant_unavailable',
                   'home_assistant_api_error','home_assistant_timeout','provider_unavailable',
                   'provider_error','internal_server_error','logbook_response_limit_exceeded',
                   'logbook_busy','invalid_request','validation_failure'}
    error=response.get('error')
    code=error.get('code') if isinstance(error,dict) else None
    records=response.get('data')
    known={f'synthetic_{entity}_{age}' for entity in ('alpha','beta') for age in (192,120,48,18,6,-1)}
    labels=[];unknown=0
    if isinstance(records,list):
        for item in records[:64]:
            message=item.get('message') if isinstance(item,dict) else None
            if isinstance(message,str) and message in known:
                labels.append(message)
            else:
                unknown+=1
    return {'success':response.get('success') is True,
            'error_code':code if isinstance(code,str) and code in known_codes else 'other_or_absent',
            'data_is_list':isinstance(records,list),'record_count':len(records) if isinstance(records,list) else None,
            'known_messages':labels,'unrecognized_records_in_first64':unknown}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def guard(env):
    require(env.get('GITHUB_ACTIONS') == 'true', 'github_runner_required')
    require(env.get('GITHUB_REPOSITORY') == 'jeter-1/hass-mcp-admin', 'repository_mismatch')
    require(env.get('GITHUB_EVENT_NAME') == 'push' and env.get('GITHUB_REF') == BRANCH, 'branch_event_mismatch')
    require(env.get('GITHUB_JOB') == 'core-logbook', 'job_mismatch')
    require(re.fullmatch('[0-9a-f]{40}', env.get('GITHUB_SHA', '')), 'source_missing')
    parts = [env.get(k, '') for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')]
    require(all(re.fullmatch('[0-9]{1,16}', p) for p in parts), 'run_identity_missing')
    return 'logbook-' + '-'.join(parts)


def command(args, timeout=90, check=True):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    require(len(result.stdout) <= MAX_BYTES and len(result.stderr) <= MAX_BYTES, 'command_output_limit')
    require(not check or result.returncode == 0, 'local_command_failed')
    return result


def docker(*args, **kw):
    return command(['docker', '--host', 'unix:///var/run/docker.sock', *args], **kw)


def save(out, name, value):
    raw = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    require(len(raw) <= MAX_BYTES, 'evidence_limit')
    with (out / name).open('xb') as stream:
        stream.write(raw)


def expected_events(events, hours, entity='', end_shift=0):
    return sorted(e['message'] for e in events
                  if end_shift < e['age_hours'] < end_shift + hours
                  and (not entity or e['entity_id'] == entity))


def verify_records(records, events, hours, entity='', end_shift=0):
    require(isinstance(records, list), 'response_not_list')
    expected = expected_events(events, hours, entity, end_shift)
    require(sorted(r.get('message', '') for r in records) == expected, 'interval_or_entity_mismatch')
    require(all(r.get('entity_id') in {'switch.synthetic_alpha','switch.synthetic_beta'} for r in records), 'unexpected_entity')
    require(not entity or all(r.get('entity_id')==entity for r in records), 'entity_filter_mismatch')
    by_message={e['message']:e for e in events}
    require(all(r['entity_id']==by_message[r['message']]['entity_id'] for r in records), 'message_entity_mismatch')
    return expected


def cleanup(identity, folder):
    # Only resources bearing this run's unique ownership label may be removed.
    def exists(kind, name):
        args = ('container','ls','--all') if kind == 'container' else ('network','ls')
        # A failed inventory is not evidence of absence.
        names=docker(*args,'--format','{{.Names}}' if kind=='container' else '{{.Name}}').stdout.decode().splitlines()
        return name in names

    for kind, name in (('container', identity), ('network', identity)):
        if not exists(kind,name):
            continue
        fmt = '{{index .Config.Labels "' + LABEL + '"}}' if kind == 'container' else '{{index .Labels "' + LABEL + '"}}'
        inspected = docker(kind, 'inspect', '--format', fmt, name)
        require(inspected.stdout.decode().strip() == identity, 'cleanup_owner_mismatch')
        args = ('rm', '-f', name) if kind == 'container' else ('network','rm',name)
        docker(*args)
        require(not exists(kind,name), 'cleanup_not_verified')
    if folder.exists():
        require(not folder.is_symlink() and folder.name == identity, 'cleanup_path_mismatch')
        shutil.rmtree(folder)
    require(not folder.exists(), 'fixture_cleanup_failed')


def verify_unchanged_source(pins):
    # Every shipped runtime/lock byte is pinned; only test/workflow files vary.
    for name, sha in pins['engineering_files'].items():
        require(digest((ROOT / name).read_bytes()) == sha, 'shipped_engineering_source_changed')


def verify_image(pins, out):
    image = 'ghcr.io/home-assistant/home-assistant@' + pins['image_index']
    raw = docker('buildx','imagetools','inspect',image,'--raw').stdout
    if digest(raw) != pins['image_index'].removeprefix('sha256:'):
        raw = raw.removesuffix(b'\n')
    require(digest(raw) == pins['image_index'].removeprefix('sha256:'), 'index_digest_mismatch')
    index = json.loads(raw)
    descriptors = [x for x in index['manifests'] if x.get('platform',{}).get('os') == 'linux'
                   and x.get('platform',{}).get('architecture') == 'amd64']
    require(len(descriptors)==1 and descriptors[0]['digest']==pins['amd64_manifest'], 'platform_mismatch')
    manifest = docker('buildx','imagetools','inspect','ghcr.io/home-assistant/home-assistant@'+pins['amd64_manifest'],'--raw').stdout
    if digest(manifest) != pins['amd64_manifest'].removeprefix('sha256:'):
        manifest = manifest.removesuffix(b'\n')
    require(digest(manifest)==pins['amd64_manifest'].removeprefix('sha256:') and len(manifest)==descriptors[0]['size'], 'manifest_mismatch')
    config_digest = json.loads(manifest)['config']['digest']
    docker('pull','--platform','linux/amd64',image,timeout=240)
    observed = docker('image','inspect',image,'--format','{{.Id}}').stdout.decode().strip()
    require(observed==config_digest, 'image_config_mismatch')
    (out/'core-index.json').write_bytes(raw)
    (out/'core-manifest.json').write_bytes(manifest)
    save(out,'image-binding.json',{'index':pins['image_index'],'manifest':pins['amd64_manifest'],
                                  'configuration':observed,'platform':'linux/amd64'})
    return image, observed


async def reader_cases(folder, out, fixture, diagnostics=None):
    # Configure only this disposable process before importing the shipped tool.
    os.environ['HA_URL'] = 'http://127.0.0.1:18123'
    os.environ['HA_TOKEN'] = (folder/'ephemeral-token').read_text()
    os.environ['AUDIT_ENABLED'] = 'false'
    os.environ['AUDIT_PATH'] = str(folder/'engineering-audit.jsonl')
    os.environ['GOVERNANCE_PATH'] = str(folder/'governance')
    sys.path.insert(0, str(ROOT/'hass_mcp_engineering_beta'))
    from ha_mcp_engineering.clients import logbook
    from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
    from ha_mcp_engineering.tools import compatibility
    from ha_mcp_engineering.request_context import begin_request, end_request
    from dataclasses import replace
    import aiohttp

    captured = []
    class ObservedClient(HomeAssistantRestClient):
        async def request(self, method, path, *args, **kwargs):
            require(method == 'GET' and path.startswith('/logbook/'), 'unexpected_dispatch')
            captured.append(path)
            return await super().request(method,path,*args,**kwargs)

    settings = replace(compatibility.SETTINGS, ha_url='http://127.0.0.1:18123',
                       ha_token=os.environ['HA_TOKEN'], audit_enabled=False,
                       ha_timeout_seconds=15, response_size_limit=60000)
    client = ObservedClient(settings)
    now = datetime.fromisoformat(fixture['now'])
    results=[]
    diagnostics = diagnostics if diagnostics is not None else {}
    # The clock is the sole patched reader dependency. Transport, Core view,
    # recorder query, sanitizer and registered response envelope are unmodified.
    for hours in (12,24,72,168):
        for entity in ('','switch.synthetic_alpha'):
            for end_shift in (0,24):
                end = now - timedelta(hours=end_shift)
                start = end - timedelta(hours=hours)
                count = len(captured)
                telemetry, context_token = begin_request(f'synthetic-interval-{len(results)}')
                try:
                    with patch.object(logbook,'datetime') as clock, patch.object(compatibility,'REST_CLIENT',client), patch.object(compatibility,'SETTINGS',settings):
                        clock.now.return_value = end
                        response = json.loads(await compatibility.get_logbook(hours=hours,entity_id=entity))
                        clock.now.assert_called_once_with(timezone.utc)
                finally:
                    end_request(context_token)
                diagnostics.update(case={'hours':hours,'entity_filter':entity,'end_shift_hours':end_shift,
                                         'previous_passed_cases':len(results)},response=response_diagnostic(response))
                require(response.get('success') is True, 'registered_tool_failure')
                require(len(captured)==count+1, 'request_count_mismatch')
                parsed=urlsplit(captured[-1])
                require(parsed.path=='/logbook/'+start.isoformat(), 'start_mismatch')
                require(parse_qs(parsed.query)=={'end_time':[end.isoformat()],**({'entity':[entity]} if entity else {})}, 'end_or_filter_mismatch')
                selected=verify_records(response['data'],fixture['events'],hours,entity,end_shift)
                coverage=response['metadata']['source_coverage']
                require(len(coverage)==1 and coverage[0]['provider']=='direct_ha_api'
                        and coverage[0]['completeness']=='complete' and coverage[0]['fallback_occurred'] is False, 'attribution_mismatch')
                require(response['timing']['home_assistant_request_count']==1, 'telemetry_attempt_mismatch')
                require(telemetry.retry_count==0 and telemetry.upstream_request_count==0, 'retry_or_upstream_attempt')
                results.append({'hours':hours,'entity_filter':entity,'end_shift_hours':end_shift,
                                'interval':{'start':start.isoformat(),'end':end.isoformat()},
                                'expected_and_observed_messages':selected,'provider':coverage[0],
                                'request_count':1,'status':'PASS'})
    # A raw old-style no-end-time query is a negative control: Core defaults to
    # one day from the start, so it must not accidentally satisfy a 72h window.
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),trust_env=False) as session:
        start=now-timedelta(hours=72)
        async with session.get(settings.api_url+'/logbook/'+start.isoformat(),
                               headers={'Authorization':'Bearer '+settings.ha_token},allow_redirects=False) as response:
            require(response.status==200,'negative_control_http_failure')
            old=await response.json()
    correct=expected_events(fixture['events'],72)
    require(sorted(r.get('message','') for r in old)!=correct, 'missing_end_control_not_distinguishable')
    save(out,'interval-results.json',{'status':'PASS','cases':results,'registered_case_count':16,
                                    'missing_end_time_control':'PASS','logical_reader_requests':len(captured),
                                    'limitations':['Test clock fixed for reproducibility; production authority admission is outside this isolated reader test.',
                                                  'Request count combines reader dispatch and shipped telemetry; no packet capture is claimed.']})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cleanup',action='store_true')
    args=parser.parse_args()
    identity=guard(os.environ)
    folder=Path(os.environ['RUNNER_TEMP'])/identity
    out=ROOT/'.artifacts/core-logbook-integration'
    if args.cleanup:
        cleanup(identity,folder)
        return
    os.umask(0o077)
    out.mkdir(parents=True,exist_ok=False)
    folder.mkdir(mode=0o700,exist_ok=False)
    pins=json.loads(PINS.read_text())
    verify_unchanged_source(pins)
    require(command(['git','rev-parse','HEAD']).stdout.decode().strip()==os.environ['GITHUB_SHA'], 'checkout_mismatch')
    phase='image_verification'
    status='FAIL'
    diagnostics={}
    try:
        image,config_digest=verify_image(pins,out)
        phase='core_start'
        docker('network','create','--internal','--label',LABEL+'='+identity,identity)
        require(docker('network','inspect','--format','{{.Internal}}',identity).stdout.strip()==b'true','network_not_internal')
        docker('run','-d','--name',identity,'--label',LABEL+'='+identity,'--network',identity,
               '--publish','127.0.0.1:18123:8123','--platform','linux/amd64','--cpus','2','--memory','2g',
               '--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges',
               '--user',f'{os.getuid()}:{os.getgid()}', '--env','HOME=/config','--env','PYTHONDONTWRITEBYTECODE=1',
               '--mount',f'type=bind,src={folder},dst=/config',
               '--mount',f'type=bind,src={ROOT/"scripts/core_logbook_fixture.py"},dst=/fixture/core.py,readonly',
               '--mount',f'type=bind,src={PINS},dst=/fixture/pins.json,readonly',
               '--entrypoint','python',image,'/fixture/core.py')
        require(docker('inspect','--format','{{.Image}}',identity).stdout.decode().strip()==config_digest,'container_image_mismatch')
        deadline=time.monotonic()+200
        while not (folder/'ready.json').exists():
            require(time.monotonic()<deadline,'core_readiness_timeout')
            require(docker('inspect','--format','{{.State.Running}}',identity).stdout.strip()==b'true','core_fixture_exited')
            time.sleep(2)
        fixture=json.loads((folder/'ready.json').read_text())
        require(fixture['core_version']==pins['core_version'] and fixture['source_commit']==pins['core_source'],'core_identity_mismatch')
        save(out,'fixture.json',fixture)
        phase='registered_reader_intervals'
        asyncio.run(asyncio.wait_for(reader_cases(folder,out,fixture,diagnostics),timeout=300))
        status='PASS'
    finally:
        if status!='PASS':
            # Only our fixture's fixed failure category is projected. No raw
            # Core logs, refresh tokens, databases or environment are exported.
            logs=docker('logs','--tail','5',identity,check=False)
            categories=[]
            for line in logs.stdout.decode(errors='replace').splitlines():
                try: entry=json.loads(line)
                except ValueError: continue
                if entry.get('status')=='FAIL' and re.fullmatch('[A-Za-z]{1,60}',str(entry.get('category',''))):
                    categories.append(entry['category'])
            error=sys.exception()
            reason=error.reason if isinstance(error,RequirementFailure) else 'unclassified'
            save(out,'failure.json',{'phase':phase,'reason':reason,'reader':diagnostics,'fixture_error_types':categories})
        cleanup(identity,folder)
        save(out,'result.json',{'status':status,'phase':phase,'cleanup':'PASS','engineering_release_source':pins['engineering_source'],
                                'candidate_sha':os.environ['GITHUB_SHA'],'core_source':pins['core_source'],
                                'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],
                                'production_requests':0,'services_or_devices_invoked':0})


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status':'FAIL','category':type(error).__name__}))
        raise SystemExit(1) from None
