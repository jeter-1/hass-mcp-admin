"""Generate a HAMCP-181 beta.8 entity-registry error-result record with the exact beta.8 writer.

Usage: pinned-python generate.py EXACT_BETA8_CHECKOUT NEW_OUTPUT_DIRECTORY

The checkout must be a clean 7370ad2 (2.4.1-beta.8) tree.  The record is
written by the unmodified shipped runtime: the beta.8 service, F3 runtime,
configuration adapter, entity-metadata gateway and the real beta.8 Home
Assistant WebSocket client, driven through beta.8's own entity-metadata test
harness.  The client talks to a disposable loopback peer that applies the
update to the synthetic registry and then answers Core's ``unknown_error``
result (the HAMCP-181 revision-3 probe shape).  beta.8 recorded that answer
as a confirmed rejection (``entity_registry_update_rejected``) with no
permission-denied reason code.  The generator never edits serialized JSON.
Inputs are synthetic, no production record is read, and no Home Assistant or
provider is contacted.  UUIDs and timestamps are left as written.
"""
from pathlib import Path
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys

SOURCE = '7370ad25eb98e03347d73e152f38881895477d74'
SHAPE = 'r_unproven_entity_registry_error_after_assignment'
TARGET = 'sensor.porch'
source, destination = map(lambda p: Path(p).resolve(), sys.argv[1:])
env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, env=env, text=True).strip() == SOURCE
assert not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=source, env=env, text=True).strip()
assert not destination.exists()
sys.path[:0] = [str(source), str(source / 'hass_mcp_engineering_beta'), str(source / 'tests')]

from aiohttp import web

from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
from ha_mcp_engineering.configuration import Settings
from tests.hamcp069a_entity_fixtures import UPDATE, EntityMetadataServiceTestCase
import ha_mcp_engineering
import tests.hamcp069a_entity_fixtures as beta8_fixtures

for module in (ha_mcp_engineering, beta8_fixtures):
    assert Path(module.__file__).resolve().is_relative_to(source), module.__file__


class AssignThenUnknownErrorPeer:
    """Loopback peer: one command per connection, Core's result-message shape."""

    def __init__(self, registry):
        self.registry = registry
        self.runner = None
        self.url = ''

    async def start(self):
        app = web.Application()
        app.router.add_get('/api/websocket', self.handle)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.url = 'http://127.0.0.1:%d' % site._server.sockets[0].getsockname()[1]

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({'type': 'auth_required'})
        await ws.receive_json()
        await ws.send_json({'type': 'auth_ok'})
        command = await ws.receive_json()
        message_id = command.pop('id')
        result = await self.registry.command(command)
        if command.get('type') == UPDATE:
            # The registry entry was assigned; Core then answers an error.
            answer = {'success': False, 'error': {'code': 'unknown_error', 'message': 'synthetic'}}
        else:
            answer = {'success': True, 'result': result}
        await ws.send_json({'id': message_id, 'type': 'result', **answer})
        await ws.close()
        return ws


async def generate():
    hashes = {}
    case = EntityMetadataServiceTestCase()
    await case.asyncSetUp()
    peer = AssignThenUnknownErrorPeer(case.ws)
    await peer.start()
    try:
        case.resource_gateway.entity_metadata.websocket_client = HomeAssistantWebSocketClient(Settings(
            ha_url=peer.url, ha_token='synthetic-no-real-token', access_secret='synthetic-no-real-secret',
            port=8100, audit_path='unused-synthetic', rate_limit_per_minute=120, rate_limit_burst=20,
            destructive_services=frozenset(), ha_timeout_seconds=5.0))
        case.ws.labels = [{'label_id': 'night', 'name': 'Night'}, {'label_id': 'outdoor', 'name': 'Outdoor'}]
        case.add_entity(TARGET, labels=['night'], icon='mdi:old')
        created = await case.create(entity_id=TARGET, name='Porch sensor', icon='mdi:new')
        await case.approve(created)
        result = await case.apply(created)
        entry = case.ws.entries[TARGET]
        assert (entry['name'], entry['icon']) == ('Porch sensor', 'mdi:new'), entry
        assert len(case.ws.updates()) == 1
        (item,) = case.runtime.children.declarations_for_task(result['task_id'])
        record = case.runtime.children.get(item['child_id'])
        codes = sorted({code for event in record.events for code in event['diagnostic_codes']})
        summary = dict(task_id=result['task_id'], plan_id=created['plan_id'], status=result.get('status'),
                       task_state=result.get('task_state'), child_id=item['child_id'], child_state=record.state,
                       normalized_outcome=record.normalized_outcome, dispatch_count=record.dispatch_count,
                       codes=codes, registry_after={'name': entry['name'], 'icon': entry['icon']})
        root = case.root / 'plans'
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.suffix in {'.lock', '.jsonl'}:
                continue
            relative = Path(SHAPE) / 'store' / path.relative_to(root)
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output)
            hashes[relative.as_posix()] = hashlib.sha256(output.read_bytes()).hexdigest()
    finally:
        await peer.runner.cleanup()
        await case.asyncTearDown()
        case.doCleanups()
    sources = {}
    for relative in ('hass_mcp_engineering_beta/ha_mcp_engineering/clients/websocket.py',
                     'tests/hamcp069a_entity_fixtures.py'):
        sources[relative] = hashlib.sha256((source / relative).read_bytes()).hexdigest()
    for namespace in ('f3', 'f3_runtime', 'f3_configuration', 'governance'):
        for path in sorted((source / 'hass_mcp_engineering_beta/ha_mcp_engineering' / namespace).rglob('*.py')):
            sources[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    provenance = dict(
        source=SOURCE, source_version='2.4.1-beta.8',
        generator='tests/fixtures/hamcp181_beta8_entity_registry_error/generate.py',
        generator_command='python generate.py <clean 7370ad2 checkout> <new output directory>',
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        fixture_type='synthetic exact historical APIs; no production records',
        shapes={SHAPE: summary}, source_files=sources, files=hashes)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'provenance.json').write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'source': SOURCE, 'files': len(hashes), 'shape': summary}, sort_keys=True))

asyncio.run(generate())
