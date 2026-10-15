"""Generate HAMCP-181 historical dispatch-phase records with the exact beta.8 writer.

Usage: pinned-python generate.py EXACT_BETA8_CHECKOUT NEW_OUTPUT_DIRECTORY

The checkout must be a clean 7370ad2 (2.4.1-beta.8) tree. Every record is
written by the unmodified shipped runtime driven through the shipped beta.8
test harnesses and fakes; the generator never edits serialized JSON. Inputs
are synthetic, no production record is read, and no network, Home Assistant
or provider is contacted. UUIDs and timestamps are left as written.
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
source, destination = map(lambda p: Path(p).resolve(), sys.argv[1:])
env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, env=env, text=True).strip() == SOURCE
assert not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=source, env=env, text=True).strip()
assert not destination.exists()
sys.path[:0] = [str(source), str(source / 'hass_mcp_engineering_beta'), str(source / 'tests')]

from ha_mcp_engineering.governance.operational_lifecycle import LifecycleGatewayError
from ha_mcp_engineering.governance.resources import ConfigurationMutationNotDispatchedError
from tests.test_beta37_exact_helper_state import ExactHelperStateRuntimeTests
from tests.test_f3_runtime_integration import F3ConfigurationActivationTests, F3OperationalActivationTests
from tests.test_hamcp069a_entity_metadata_v9_replay import EntityMetadataV9Tests
from tests.test_hamcp177_dashboard_write_admission import EXTRAS, GUIDE, DashboardPeer, Harness, catalog
from tests.test_typed_fan import FanTests
from tests.test_typed_power import PowerTests

AUTOMATION = ('automation', 'apply_hvac_comfort')
REJECTED_4XX = dict(operation='automation_configuration_write', resource_id='apply_hvac_comfort',
                    reason='configuration_write_rejected', provider_status=400,
                    provider_response_received=True)


def copy_store(root, shape, hashes):
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix in {'.lock', '.jsonl'}:
            continue
        relative = Path(shape) / 'store' / path.relative_to(root)
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
        hashes[relative.as_posix()] = hashlib.sha256(output.read_bytes()).hexdigest()


def governed_summary(runtime, result):
    declarations = runtime.children.declarations_for_task(result['task_id'])
    children = []
    for item in declarations:
        record = runtime.children.get(item['child_id'])
        children.append(None if record is None else dict(
            child_id=item['child_id'], state=record.state, dispatch_count=record.dispatch_count,
            codes=sorted({code for event in record.events for code in event['diagnostic_codes']})))
    return dict(task_id=result['task_id'], plan_id=result.get('plan_id'),
                task_state=result['task_state'], children=children)


async def governed(case_type, shape, act, hashes, store='plans'):
    case = case_type()
    await case.asyncSetUp()
    try:
        runtime, result = await act(case)
        summary = governed_summary(runtime, result)
        root = getattr(case, 'root', None) or Path(case.temp.name)
        copy_store(root / store, shape, hashes)
        return summary
    finally:
        await case.asyncTearDown()
        case.doCleanups()


async def configuration(case, *, plan='automation', fail=None, lost=False):
    created = await (case.create_hvac_plan() if plan == 'hvac' else case.create_automation_plan())
    await case.approve(created)
    if fail is not None:
        case.gateway.fail_write_target = AUTOMATION
        case.gateway.fail_write_exception = ConfigurationMutationNotDispatchedError(details=dict(fail))
    if lost:
        case.gateway.fail_after_write_target = AUTOMATION
    result = await case.service.apply(created['plan_id'], created['plan_hash'])
    return case.runtime, {**result, 'plan_id': created['plan_id']}


async def helper_already_present(case):
    plan = await case.create_and_grant()
    case.helper.set_observed_state('on', case.clock)
    result = await case.service.apply(plan['plan_id'], plan['plan_hash'])
    assert case.helper.dispatch_count == 0
    return case.runtime, {**result, 'plan_id': plan['plan_id']}


async def dashboard_setter_not_sent(case):
    peer = DashboardPeer(catalog(EXTRAS))
    _provider, service, runtime, created = await case.planned(peer)
    case.telemetry.core_dispatch_authorizer = case.authorizer

    async def retire(_count):
        case.core_ok = False
    peer.hooks[GUIDE] = retire
    result = await service.apply(created['plan_id'], created['plan_hash'])
    return runtime, {**result, 'plan_id': created['plan_id']}


async def operational_confirmed_failure(case):
    async def rejected(before_dispatch):
        await before_dispatch()
        case.lifecycle.dispatch_count += 1
        raise LifecycleGatewayError('operation_rejected', dispatched=True)
    case.lifecycle._dispatch = rejected
    created = await case.service.create_reload_plan(reload_target='automation')
    plan = await case._grant(created)
    result = await case.service.apply(plan['plan_id'], plan['plan_hash'])
    return case.runtime, {**result, 'plan_id': plan['plan_id']}


async def entity_metadata_v9(case):
    created, result = await case.already_present()
    case.assert_no_write_reached_home_assistant()
    return case.runtime, {**result, 'plan_id': created['plan_id']}


async def typed(case_type, shape, call, hashes):
    case = case_type()
    await case.asyncSetUp()
    try:
        result = await case.call(call(case))
        assert result['state'] == 'succeeded_verified', result
        await case.service.close()
        copy_store(Path(case.tmp.name), shape, hashes)
        return dict(task_id=result['task_id'], state=result['state'],
                    provider_attempt_count=result['provider_attempt_count'])
    finally:
        case.doCleanups()


async def generate():
    hashes, shapes = {}, {}
    shapes['a_helper_already_present'] = await governed(
        ExactHelperStateRuntimeTests, 'a_helper_already_present', helper_already_present, hashes)
    shapes['b_dashboard_setter_not_sent'] = await governed(
        Harness, 'b_dashboard_setter_not_sent', dashboard_setter_not_sent, hashes)
    shapes['r_proven_configuration_4xx'] = await governed(
        F3ConfigurationActivationTests, 'r_proven_configuration_4xx',
        lambda case: configuration(case, fail=REJECTED_4XX), hashes)
    shapes['r_unproven_operational_rejection'] = await governed(
        F3OperationalActivationTests, 'r_unproven_operational_rejection', operational_confirmed_failure, hashes)
    shapes['c_response_lost_verified'] = await governed(
        F3ConfigurationActivationTests, 'c_response_lost_verified',
        lambda case: configuration(case, lost=True), hashes)
    shapes['sent_verified_configuration'] = await governed(
        F3ConfigurationActivationTests, 'sent_verified_configuration', configuration, hashes)
    shapes['v9_entity_metadata_already_present'] = await governed(
        EntityMetadataV9Tests, 'v9_entity_metadata_already_present', entity_metadata_v9, hashes)
    shapes['mixed_three_child_parent'] = await governed(
        F3ConfigurationActivationTests, 'mixed_three_child_parent',
        lambda case: configuration(case, plan='hvac', fail=REJECTED_4XX), hashes)
    shapes['fan_sent_verified'] = await typed(
        FanTests, 'fan_sent_verified', lambda case: case.request('turn_on', 50), hashes)
    shapes['power_sent_verified'] = await typed(
        PowerTests, 'power_sent_verified', lambda case: case.request('light.synthetic', 'turn_on'), hashes)
    sources = {}
    for namespace in ('f3', 'f3_runtime', 'f3_configuration', 'f3_dashboard', 'governance', 'fan', 'power'):
        for path in sorted((source / 'hass_mcp_engineering_beta/ha_mcp_engineering' / namespace).rglob('*.py')):
            sources[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    provenance = dict(
        source=SOURCE, source_version='2.4.1-beta.8',
        generator='tests/fixtures/f3_beta8_dispatch_phase/generate.py',
        generator_command='python generate.py <clean 7370ad2 checkout> <new output directory>',
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        fixture_type='synthetic exact historical APIs; no production records',
        shapes=shapes, source_files=sources, files=hashes)
    (destination / 'provenance.json').write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'source': SOURCE, 'files': len(hashes),
                      'shapes': {name: value.get('task_state', value.get('state')) for name, value in shapes.items()}}))

asyncio.run(generate())
