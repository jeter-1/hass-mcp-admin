"""Generate synthetic legacy state using the unmodified beta.5 writer.

Usage: pinned-python generate.py EXACT_BETA5_CHECKOUT NEW_OUTPUT_DIRECTORY
Never accepts production input. No network or provider dispatch is performed.
UUIDs/timestamps in writer envelopes are deliberately not rewritten afterward.
"""
from pathlib import Path
import asyncio
from datetime import timedelta
import hashlib
import json
import os
import shutil
import subprocess
import sys

SOURCE = '34f96a90548f72d36f6f5a9a428920a3a7d14de9'
source, destination = map(lambda p: Path(p).resolve(), sys.argv[1:])
env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, env=env, text=True).strip() == SOURCE
assert not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=source, env=env, text=True).strip()
assert not destination.exists()
sys.path[:0] = [str(source), str(source / 'hass_mcp_engineering_beta')]
from ha_mcp_engineering.f3.models import ExecutionIdentity, ExecutorTiming, LockOwner, LockTiming
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
from tests.test_dev14_configuration_plans import ConfigurationPlanTestCase
from tests.test_f3_runtime_integration import _ExactFakeConfigurationGateway, _provider_identity

async def generate():
    case = ConfigurationPlanTestCase()
    await case.asyncSetUp()
    try:
        now = case.service.now()
        runtime = F3RuntimeIntegration(service=case.service, storage_root=str(case.root / 'plans'),
            configuration_gateway=_ExactFakeConfigurationGateway(case.gateway), backup_gateway=None,
            lifecycle_gateway=None, provider_identity_reader=_provider_identity, retention_days=90)
        case.service.f3_runtime = runtime
        await runtime.recover_once('startup')
        created = await case.create_automation_plan()
        await case.approve(created)
        plan = case.service._load(created['plan_id'])
        task, prepared, complete = await runtime._initialize(plan, created['plan_hash'])
        task = runtime._enter_public_preflight(task)
        declaration = runtime.children.declarations_for_task(task.task_id)[0]
        identity = ExecutionIdentity(declaration['child_id'], declaration['plan_id'], declaration['attempt_id'],
                                     declaration['request_id'], 'synthetic-legacy-owner')
        claim = runtime.children.claim(identity=identity, prepared=prepared[0],
            timing=ExecutorTiming(120, 60, 3, 3), now=now)
        handle = runtime.locks.acquire_once(complete,
            owner=LockOwner(identity.owner_id, identity.task_id, identity.plan_id, prepared[0].operation, identity.attempt_id),
            timing=LockTiming(60, 10, 0), now=now)
        # The exact historical APIs write both sides of the acquisition gap.
        # Simulate loss/failure before record_locks; do not edit serialized JSON.
        runtime.children.terminalize_pre_dispatch(identity.task_id, owner_id=identity.owner_id,
            claim_generation=claim.claim_generation, outcome='failed_pre_dispatch',
            diagnostic_codes=('lock_storage_failure',), now=now)
        runtime._project(plan, task)
        assert not runtime.children.get(identity.task_id).lock_tokens
        assert runtime.children.get(identity.task_id).dispatch_count == 0
        assert not any(call[0] == 'write' for call in case.gateway.calls)
        destination.mkdir(parents=True)
        hashes = {}
        for path in sorted((case.root / 'plans').rglob('*')):
            if not path.is_file() or path.name.startswith('.'):
                continue
            relative = path.relative_to(case.root / 'plans')
            output = destination / 'store' / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output)
            hashes[str(Path('store') / relative)] = hashlib.sha256(output.read_bytes()).hexdigest()
        sources = {}
        for namespace in ('f3', 'f3_runtime', 'f3_configuration', 'governance'):
            for path in sorted((source / 'hass_mcp_engineering_beta/ha_mcp_engineering' / namespace).rglob('*.py')):
                sources[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
        provenance = dict(source=SOURCE, generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            fixture_type='synthetic exact historical APIs; no production records', source_files=sources,
            files=hashes, child_id=identity.task_id, public_task_id=task.task_id,
            plan_id=created['plan_id'], recover_at=(now + timedelta(seconds=121)).isoformat(),
            retained_locks=len(handle.tokens), calls=case.gateway.calls)
        (destination / 'provenance.json').write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
        print(json.dumps({'source': SOURCE, 'files': len(hashes), 'locks': len(handle.tokens), 'dispatches': 0}))
    finally:
        await case.asyncTearDown()

asyncio.run(generate())
