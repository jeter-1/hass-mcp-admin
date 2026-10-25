"""Generate HAMCP-181 PR2 candidate-writer records for the beta.8 downgrade test.

Usage (from the repository root, with the pinned Python 3.12 interpreter):

    python tests/fixtures/f3_hamcp181_candidate/generate.py NEW_OUTPUT_DIRECTORY

Design section 4.3 item 4: the records are written by the exact PR2 candidate
writer, which is the runtime of this checkout.  The checkout's tracked files
must be clean (``git status --porcelain --untracked-files=no`` is empty), so
the recorded HEAD commit is exactly the writer that produced every record.
Every record is written by the unmodified runtime driven through the shipped
test harnesses and synthetic fakes; the generator never edits serialized
JSON.  Inputs are synthetic, no production record is read, and no Home
Assistant, provider or external network is contacted (one shape uses the
disposable loopback WebSocket peer of HAMCP-183).  UUIDs and timestamps are
left as written.  Every record is terminal.
"""
from pathlib import Path
import asyncio
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys

REPOSITORY = Path(__file__).resolve().parents[3]
GENERATOR = Path(__file__).resolve().relative_to(REPOSITORY).as_posix()
destination = Path(sys.argv[1]).resolve()
env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}


def git(*arguments):
    return subprocess.check_output(['git', *arguments], cwd=REPOSITORY, env=env, text=True).strip()


CANDIDATE = git('rev-parse', 'HEAD')
TRACKED_STATUS = git('status', '--porcelain', '--untracked-files=no')
RUNTIME_STATUS = git('status', '--short', '--', 'hass_mcp_engineering_beta/')
assert not TRACKED_STATUS, f'tracked files differ from {CANDIDATE}:\n{TRACKED_STATUS}'
assert not RUNTIME_STATUS, RUNTIME_STATUS
assert not destination.exists(), destination
sys.path[:0] = [str(REPOSITORY), str(REPOSITORY / 'hass_mcp_engineering_beta'), str(REPOSITORY / 'tests')]

from ha_mcp_engineering.governance.operational_lifecycle import LifecycleGatewayError  # noqa: E402
from ha_mcp_engineering.governance.resources import ConfigurationMutationNotDispatchedError  # noqa: E402
from tests.test_beta37_exact_helper_state import ExactHelperStateRuntimeTests  # noqa: E402
from tests.test_f3_runtime_integration import (  # noqa: E402
    F3ConfigurationActivationTests,
    F3OperationalActivationTests,
)
from tests.test_hamcp069a_entity_metadata_v9_replay import EntityMetadataV9Tests  # noqa: E402
from tests.test_hamcp177_dashboard_write_admission import (  # noqa: E402
    EXTRAS,
    GUIDE,
    DashboardPeer,
    Harness,
    catalog,
)
from tests.test_hamcp181_proven_no_call_producers import (  # noqa: E402
    ConfigurationChainTests,
    _raise_callback_failure,
    at_intent,
)
from tests.test_hamcp183_entity_registry_error_truth import LoopbackRegistryTestCase  # noqa: E402
from tests.test_typed_fan import FanTests  # noqa: E402
from tests.test_typed_power import PowerTests  # noqa: E402

AUTOMATION = ('automation', 'apply_hvac_comfort')
REJECTED_4XX = dict(operation='automation_configuration_write', resource_id='apply_hvac_comfort',
                    reason='configuration_write_rejected', provider_status=400,
                    provider_response_received=True)
# Volatile runtime files that are not durable records: per-process locks and
# append-only audit logs.  Everything else is copied byte for byte.
EXCLUDED_SUFFIXES = {'.lock', '.jsonl'}


def copy_store(root, shape, hashes):
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix in EXCLUDED_SUFFIXES:
            continue
        relative = Path(shape) / 'store' / path.relative_to(root)
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
        hashes[relative.as_posix()] = hashlib.sha256(output.read_bytes()).hexdigest()


def governed_summary(runtime, service, result):
    task = service.task_repository.get(result['task_id'])
    assert task.state.value in {'succeeded_verified', 'failed_pre_dispatch', 'failed_post_dispatch',
                                'manual_review_required', 'cancelled_pre_dispatch'}, task.state
    children = []
    for item in runtime.children.declarations_for_task(result['task_id']):
        record = runtime.children.get(item['child_id'])
        if record is not None:
            assert record.terminal, (result['task_id'], record.state)
        children.append(None if record is None else dict(
            child_id=item['child_id'], capability=item.get('capability_id'), state=record.state,
            normalized_outcome=record.normalized_outcome, dispatch_count=record.dispatch_count,
            codes=sorted({code for event in record.events for code in event['diagnostic_codes']})))
    view = service.get_execution_task(result['task_id'])
    plan = service.get_plan(result['plan_id'])
    return dict(task_id=result['task_id'], plan_id=result['plan_id'], task_state=task.state.value,
                terminal_outcome=task.terminal_outcome, dispatch_phase=view.get('dispatch_phase'),
                provider_dispatch_occurred=view.get('provider_dispatch_occurred'),
                plan_status=plan.get('plan', plan)['status'],
                children=children)


async def governed(case_type, shape, act, hashes, recipe):
    case = case_type()
    await case.asyncSetUp()
    try:
        runtime, service, result = await act(case)
        summary = governed_summary(runtime, service, result)
        root = getattr(case, 'root', None) or Path(case.temp.name)
        copy_store(root / 'plans', shape, hashes)
        return {**summary, 'recipe': recipe, 'harness': f'{case_type.__module__}.{case_type.__qualname__}'}
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
    return case.runtime, case.service, {**result, 'plan_id': created['plan_id']}


async def configuration_callback_not_sent(case):
    # b2-L / b5: the configuration adapter's callback fails after the durable
    # intent and before the provider invocation; the write is never called.
    created = await case.create_automation_plan()
    await case.approve(created)
    with at_intent(_raise_callback_failure):
        result = await case.service.apply(created['plan_id'], created['plan_hash'])
    assert [call for call in case.gateway.calls if call[0] == 'write'] == []
    return case.runtime, case.service, {**result, 'plan_id': created['plan_id']}


async def helper_appeared_not_sent(case):
    # The shipped resource gateway's own create preflight refuses after the
    # intent (the helper now exists); the HVAC plan stops at its first child.
    case.routed.add(('input_number', 'input_number.hvac_target'))
    case.websocket.responses['input_number/list'] = [{'id': 'hvac_target', 'name': 'x'}]
    created = await case.create_hvac_plan()
    await case.approve(created)
    result = await case.service.apply(created['plan_id'], created['plan_hash'])
    return case.runtime, case.service, {**result, 'plan_id': created['plan_id']}


async def helper_already_present(case):
    plan = await case.create_and_grant()
    case.helper.set_observed_state('on', case.clock)
    result = await case.service.apply(plan['plan_id'], plan['plan_hash'])
    assert case.helper.dispatch_count == 0
    return case.runtime, case.service, {**result, 'plan_id': plan['plan_id']}


async def dashboard_setter_not_sent(case):
    peer = DashboardPeer(catalog(EXTRAS))
    _provider, service, runtime, created = await case.planned(peer)
    case.telemetry.core_dispatch_authorizer = case.authorizer

    async def retire(_count):
        case.core_ok = False
    peer.hooks[GUIDE] = retire
    result = await service.apply(created['plan_id'], created['plan_hash'])
    return runtime, service, {**result, 'plan_id': created['plan_id']}


async def operational_confirmed_failure(case):
    async def rejected(before_dispatch):
        await before_dispatch()
        case.lifecycle.dispatch_count += 1
        raise LifecycleGatewayError('operation_rejected', dispatched=True)
    case.lifecycle._dispatch = rejected
    created = await case.service.create_reload_plan(reload_target='automation')
    plan = await case._grant(created)
    result = await case.service.apply(plan['plan_id'], plan['plan_hash'])
    return case.runtime, case.service, {**result, 'plan_id': plan['plan_id']}


async def entity_metadata_v9(case):
    created, result = await case.already_present()
    case.assert_no_write_reached_home_assistant()
    return case.runtime, case.service, {**result, 'plan_id': created['plan_id']}


async def entity_registry_error(case):
    # HAMCP-183: Core answers the sent registry update with ``unknown_error``
    # and the registry is unchanged; the error is never a non-mutation proof.
    created = await case.approved()
    case.fail_without_change('unknown_error')
    result = await case.apply(created)
    return case.runtime, case.service, {**result, 'plan_id': created['plan_id']}


async def typed(case_type, shape, call, hashes, recipe):
    case = case_type()
    await case.asyncSetUp()
    try:
        request = call(case)
        result = await case.call(request)
        assert result['state'] == 'succeeded_verified', result
        assert result['terminal'] is True, result
        assert not case.service.active
        await case.service.close()
        copy_store(Path(case.tmp.name), shape, hashes)
        return dict(task_id=result['task_id'], state=result['state'],
                    terminal_outcome=result['terminal_outcome'],
                    provider_attempt_count=result['provider_attempt_count'],
                    request=request.model_dump(), recipe=recipe,
                    harness=f'{case_type.__module__}.{case_type.__qualname__}')
    finally:
        case.doCleanups()


async def generate():
    hashes, shapes = {}, {}
    shapes['a_helper_already_present'] = await governed(
        ExactHelperStateRuntimeTests, 'a_helper_already_present', helper_already_present, hashes,
        '(a) preflight no-op child and T2 (a) task: helper-state plan, desired state already observed')
    shapes['b_configuration_callback_not_sent'] = await governed(
        F3ConfigurationActivationTests, 'b_configuration_callback_not_sent',
        configuration_callback_not_sent, hashes,
        '(b) child and T2 (b) task: automation update, adapter callback fails after the intent')
    shapes['b_hvac_helper_appeared_not_sent'] = await governed(
        ConfigurationChainTests, 'b_hvac_helper_appeared_not_sent', helper_appeared_not_sent, hashes,
        'T2 (b) task: HVAC plan, shipped gateway create preflight refuses after the intent')
    shapes['b_dashboard_setter_not_sent'] = await governed(
        Harness, 'b_dashboard_setter_not_sent', dashboard_setter_not_sent, hashes,
        'dashboard (b) child: Core authority retires during the guide read; setter never called')
    shapes['r_proven_configuration_4xx'] = await governed(
        F3ConfigurationActivationTests, 'r_proven_configuration_4xx',
        lambda case: configuration(case, fail=REJECTED_4XX), hashes,
        '(r-proven) configuration child (P1): automation write answered with REST 400')
    shapes['r_unproven_operational_rejection'] = await governed(
        F3OperationalActivationTests, 'r_unproven_operational_rejection',
        operational_confirmed_failure, hashes,
        '(r-unproven) operational child: reload provider rejection after the send')
    shapes['r_unproven_entity_registry_error'] = await governed(
        LoopbackRegistryTestCase, 'r_unproven_entity_registry_error', entity_registry_error, hashes,
        '(r-unproven) entity-metadata registry-error child: unknown_error result, registry unchanged')
    shapes['c_response_lost_verified'] = await governed(
        F3ConfigurationActivationTests, 'c_response_lost_verified',
        lambda case: configuration(case, lost=True), hashes,
        '(c) child verified by readback: automation write lands, response lost')
    shapes['sent_verified_configuration'] = await governed(
        F3ConfigurationActivationTests, 'sent_verified_configuration', configuration, hashes,
        'sent-and-verified child: automation write and exact readback')
    shapes['v9_entity_metadata_already_present'] = await governed(
        EntityMetadataV9Tests, 'v9_entity_metadata_already_present', entity_metadata_v9, hashes,
        'V9 task: entity rename already present before apply')
    shapes['mixed_three_child_parent'] = await governed(
        F3ConfigurationActivationTests, 'mixed_three_child_parent',
        lambda case: configuration(case, plan='hvac', fail=REJECTED_4XX), hashes,
        'three-child mixed parent: HVAC plan, two verified children, automation answered with REST 400')
    shapes['fan_sent_verified'] = await typed(
        FanTests, 'fan_sent_verified', lambda case: case.request('turn_on', 50), hashes,
        'fan record and prepared request declaration: typed fan turn_on 50%')
    shapes['power_sent_verified'] = await typed(
        PowerTests, 'power_sent_verified', lambda case: case.request('light.synthetic', 'turn_on'),
        hashes, 'power record and prepared request declaration: typed power turn_on')
    sources = {}
    for path in sorted((REPOSITORY / 'hass_mcp_engineering_beta/ha_mcp_engineering').rglob('*.py')):
        sources[path.relative_to(REPOSITORY).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    provenance = dict(
        candidate_commit=CANDIDATE,
        candidate_writer='HAMCP-181 PR2 candidate (unreleased); records are kept unchanged after release',
        candidate_tracked_status=TRACKED_STATUS,
        candidate_runtime_status=RUNTIME_STATUS,
        candidate_runtime_status_command='git status --short -- hass_mcp_engineering_beta/',
        generator=GENERATOR,
        generator_command=f'python {GENERATOR} <new output directory>',
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        excluded_suffixes=sorted(EXCLUDED_SUFFIXES),
        fixture_type='synthetic inputs through the candidate writer; no production records',
        shapes=shapes, source_files=sources, files=hashes)
    (destination / 'provenance.json').write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'candidate': CANDIDATE, 'files': len(hashes),
                      'shapes': {name: [value.get('task_state', value.get('state')),
                                        value.get('terminal_outcome'), value.get('dispatch_phase')]
                                 for name, value in shapes.items()}}, indent=1))


asyncio.run(generate())
