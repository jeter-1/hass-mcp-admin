"""HAMCP-311 control_helper over real Engineering provider, gateway and F3 stores.

Helper value changes are ordinary control: no plan and no approval panel. All
targets, transport responses, authority inputs, dependency evidence and audit
files are synthetic. The transport models Home Assistant Core 2026.10.3 helper
service semantics; it is not live evidence.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'hass_mcp_engineering_beta'), str(ROOT)]
from tests.test_typed_fan import Clock
from tests.test_typed_power import PowerTransport
from ha_mcp_engineering.errors import ErrorCode
from ha_mcp_engineering.helper_value.contracts import (ACTIONS, HELPER_ACTIONS, HELPER_RELEASES, HelperRefusal,
                                                       HelperRequest, digest)
from ha_mcp_engineering.helper_value.safety import CONFIRMATION, assess
from ha_mcp_engineering.helper_value.service import (HELPER_VALUE_OPERATIONS, HelperCoreAuthority, HelperService,
                                                     request_summary, status_of)
from ha_mcp_engineering.ha_core_readmission.typed_operations import operation_profile
from ha_mcp_engineering.providers.upstream_helper_value import HelperProvider
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.tools.helper import control_helper
from ha_mcp_engineering.tools.governance import get_execution_task

PROFILE = operation_profile('helper_value')
STAMP = '2026-09-16T11:00:00.000000+00:00'


def states():
    return {
        'input_boolean.synthetic': {'state': 'off'},
        'input_boolean.guest_mode': {'state': 'off'},
        'input_boolean.engineering_acceptance': {'state': 'off'},
        'input_select.synthetic': {'state': 'Home', 'attributes': {'options': ['Home', 'Away', 'Night']}},
        'input_number.synthetic': {'state': '20.0', 'attributes': {'min': 10, 'max': 30, 'step': 0.5}},
        'input_text.synthetic': {'state': 'abc', 'attributes': {'min': 1, 'max': 8, 'pattern': '[a-z]+', 'mode': 'text'}},
        'input_text.secret': {'state': 'old', 'attributes': {'min': 0, 'max': 20, 'pattern': None, 'mode': 'password'}},
        'input_datetime.synthetic': {'state': '2026-09-16 07:30:00', 'attributes': {'has_date': True, 'has_time': True}},
        'input_datetime.alarm': {'state': '07:30:00', 'attributes': {'has_date': False, 'has_time': True}},
        'input_button.synthetic': {'state': STAMP},
        'counter.synthetic': {'state': '5', 'attributes': {'initial': 0, 'step': 1, 'minimum': 0, 'maximum': 6}},
        'timer.synthetic': {'state': 'idle', 'attributes': {'duration': '0:05:00', 'last_transition': None}},
        'timer.running': {'state': 'active', 'attributes': {'duration': '0:05:00', 'remaining': '0:05:00',
                                                            'finishes_at': '2026-09-16T12:05:00+00:00'}},
    }


SERVICE_DATA = {
    ('input_boolean', 'turn_on'): set(), ('input_boolean', 'turn_off'): set(),
    ('input_select', 'select_option'): {'option'}, ('input_number', 'set_value'): {'value'},
    ('input_text', 'set_value'): {'value'}, ('input_datetime', 'set_datetime'): {'date', 'time'},
    ('input_button', 'press'): set(), ('counter', 'increment'): set(), ('counter', 'decrement'): set(),
    ('counter', 'reset'): set(), ('counter', 'set_value'): {'value'}, ('timer', 'start'): {'duration'},
    ('timer', 'pause'): set(), ('timer', 'cancel'): set(), ('timer', 'finish'): set(),
}


class HelperTransport(PowerTransport):
    """Reviewed ha-mcp catalog plus a synthetic Core 2026.10.3 helper model."""

    def __init__(self, version):
        super().__init__(version)
        self.states = {entity: {'entity_id': entity, 'last_updated': 'synthetic-revision-1', **deepcopy(value)}
                       for entity, value in states().items()}
        self.wrong_effect = None

    def effect(self, args):
        entity, domain, service, data = args['entity_id'], args['domain'], args['service'], args['data']
        target = self.states[entity]; attrs = target.setdefault('attributes', {})
        if domain == 'input_boolean':
            target['state'] = 'on' if service == 'turn_on' else 'off'
        elif domain == 'input_select':
            target['state'] = data['option']
        elif domain == 'input_number':
            target['state'] = f"{float(data['value']):.15}"
        elif domain == 'input_text':
            target['state'] = data['value']
        elif domain == 'input_datetime':
            target['state'] = ' '.join(part for part in (data.get('date'), data.get('time')) if part)
        elif domain == 'input_button':
            target['state'] = f'2026-09-16T12:00:{self.writes:02}.000000+00:00'
        elif domain == 'counter':
            value = {'increment': int(target['state']) + attrs['step'],
                     'decrement': int(target['state']) - attrs['step'],
                     'reset': attrs['initial']}.get(service, data.get('value'))
            value = max(attrs['minimum'], min(attrs['maximum'], value))
            target['state'] = str(value)
        elif domain == 'timer':
            if service == 'start':
                if 'duration' in data:
                    attrs['duration'] = data['duration']
                target['state'] = 'active'
                attrs['finishes_at'] = f'2026-09-16T12:1{self.writes % 10}:00+00:00'
            elif service == 'pause':
                target['state'] = 'paused'
            else:
                target['state'] = 'idle'
                attrs['last_transition'] = 'cancelled' if service == 'cancel' else 'finished'
                attrs.pop('finishes_at', None)
        target['last_updated'] = f'synthetic-revision-{self.writes + 1}'
        if self.wrong_effect:
            target['state'] = self.wrong_effect

    async def execute_read(self, tool, args, *, catalog_validator, before_dispatch, **kwargs):
        catalog_validator(self.catalog)
        if self.before:
            await self.before(tool)
        await before_dispatch()
        self.calls.append((tool, deepcopy(args)))
        if tool == 'ha_get_state':
            entity = args['entity_id']; raw = self.states[entity]
            if 'attribute_keys' in args:
                assert args['fields'] == ['entity_id', 'state', 'attributes', 'last_updated']
                attrs = {key: raw.get('attributes', {})[key] for key in args['attribute_keys']
                         if key in raw.get('attributes', {})}
                data = {'entity_id': raw['entity_id'], 'state': raw['state'], 'attributes': attrs,
                        'last_updated': raw['last_updated']}
            else:
                assert set(args) == {'entity_id', 'fields'} and args['fields'] == ['entity_id', 'state', 'last_updated']
                data = {key: deepcopy(raw[key]) for key in ('entity_id', 'state', 'last_updated')}
            result = {'data': data}
        elif tool == 'ha_list_services':
            assert args == {'domain': args['domain'], 'limit': 50, 'offset': 0, 'detail_level': 'summary'}
            assert args['domain'] in HELPER_ACTIONS
            result = {'success': True, 'services': {args['domain'] + '.' + action: {}
                                                    for action in HELPER_ACTIONS[args['domain']]}}
            if self.mode == 'missing_service':
                result['services'] = {}
        elif tool == 'ha_call_service':
            # The closed contract: exact keys, allowlisted service, fixed data keys.
            assert set(args) == {'domain', 'service', 'entity_id', 'data', 'wait', 'return_response', 'verbose'}
            assert args['entity_id'].split('.')[0] == args['domain']
            assert args['service'] in HELPER_ACTIONS[args['domain']], args
            assert set(args['data']) <= SERVICE_DATA[(args['domain'], args['service'])], args
            assert args['wait'] is False and args['return_response'] is False and args['verbose'] is False
            self.writes += 1
            if self.mode != 'ack_only':
                self.effect(args)
            if self.mode == 'timeout':
                raise TimeoutError('synthetic-private-provider-text')
            if self.mode == 'malformed':
                return SimpleNamespace(call_result={'structuredContent': None})
            result = {'success': True}
        else:
            raise AssertionError('unrelated provider reachability: ' + tool)
        return SimpleNamespace(call_result={'structuredContent': result, 'isError': False})


@dataclass(frozen=True)
class Lease:
    nonce: str
    generation: int
    core_version: str
    capability_ids: tuple


class SignedCore:
    """Synthetic signed applicability naming core.typed_helper_value_operation."""

    def __init__(self, signed=True):
        self.allowed, self.signed, self.version, self.generation = True, signed, '2026.10.3', 1
        self.leases, self.commits, self.requirements = set(), set(), []

    async def reconcile_once(self, trigger):
        return None

    def route_status(self, requirements):
        return {'available': self.allowed and self.signed and PROFILE.capability_id in requirements}

    def acquire(self, requirements, **kwargs):
        self.requirements.append(tuple(requirements))
        expected = kwargs['expected_core_version']
        if (not self.allowed or not self.signed or PROFILE.capability_id not in requirements
                or expected not in {None, self.version}):
            return None
        token = Lease(uuid.uuid4().hex, self.generation, self.version, tuple(requirements))
        self.leases.add(token)
        return token

    def consume(self, token):
        if token not in self.leases or not self.allowed:
            return None
        self.leases.remove(token); self.commits.add(token)
        return token

    def revalidate(self, token, commit):
        return self.allowed and token == commit and token in self.commits and token.generation == self.generation

    def release(self, token):
        self.leases.discard(token)

    def finish(self, token):
        self.commits.discard(token)


def binding(profiles, consequence, *, exact=1, counts=None):
    return {'physical_consequence': consequence, 'downstream_profiles': profiles,
            'exact_dependency_obligation_count': exact, 'coverage_complete': False,
            'relevant_consumer_counts_by_class': counts or {}}


GUEST_MODE = {'available': True, 'names': {'automation.unlock_for_guests': 'Unlock front door for guests',
                                           'automation.guest_lights': 'Guest lights'},
              'binding': binding([
                  {'automation_id': 'automation.unlock_for_guests', 'evidence_class': 'referenced',
                   'physical_consequence': 'safety_critical'},
                  {'automation_id': 'automation.guest_lights', 'evidence_class': 'referenced',
                   'physical_consequence': 'routine'},
                  {'automation_id': 'automation.template_reader', 'evidence_class': 'opaque',
                   'physical_consequence': 'safety_critical'},
              ], 'safety_critical', counts={'referenced': 2, 'opaque': 1})}
OPAQUE_ONLY = {'available': True, 'names': {'automation.acceptance_probe': 'Acceptance probe'},
               'binding': binding([
                   {'automation_id': 'automation.acceptance_probe', 'evidence_class': 'opaque',
                    'physical_consequence': 'safety_critical'},
               ], 'unknown', exact=0, counts={'opaque': 1})}
UNAVAILABLE = {'available': False, 'failure_category': 'DependencyIndexUnavailable'}


class HelperTests(unittest.IsolatedAsyncioTestCase):
    version = '8.5.0'

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clock, self.core = Clock(), SignedCore()
        self.transport = HelperTransport(self.version)
        self.provider = HelperProvider(self.transport, lambda *args: self.transport.authority)
        self.evidence, self.reads = {}, []
        self.service = self.make_service()
        self.telemetry, token = begin_request('synthetic-helper-request')
        self.addCleanup(end_request, token)

    def make_service(self):
        async def reader(entity_id):
            self.reads.append(entity_id)
            value = self.evidence.get(entity_id, OPAQUE_ONLY)
            if isinstance(value, Exception):
                raise value
            return deepcopy(value)
        service = HelperService(self.tmp.name, self.provider, HelperCoreAuthority(self.core),
                                now=self.clock, sleep=self.clock.sleep, dependency_reader=reader)
        self.addAsyncCleanup(service.close)
        return service

    def request(self, entity='input_boolean.synthetic', action='turn_on', value=None, **kwargs):
        return HelperRequest(entity_id=entity, action=action, value=value,
                             operation_id=f'{int(self.clock().timestamp())}-{uuid.uuid4().hex}', **kwargs)

    async def call(self, request):
        self.telemetry.ordinary_helper_value_binding = digest(request.model_dump())
        return await self.service.control(request)

    def dispatches(self):
        return [args for tool, args in self.transport.calls if tool == 'ha_call_service']

    def settled(self):
        self.assertFalse(self.core.leases or self.core.commits)
        self.assertEqual(self.service.locks.records(), ())
        self.assertEqual(self.service.health()['fallback_count'], 0)

    async def assert_verified(self, request, expected_state):
        result = await self.call(request)
        self.assertEqual(result['status'], 'succeeded_verified', result)
        self.assertEqual(result['state'], 'succeeded_verified')
        self.assertEqual(result['provider'], 'upstream_typed_helper_value')
        self.assertEqual(result['provider_contract'], HELPER_RELEASES[self.version][2])
        self.assertEqual(result['provider_attempt_count'], 1)
        self.assertTrue(result['dispatch_intent_recorded'])
        self.assertEqual(result['core_binding'], {'version': '2026.10.3',
                                                  'contract_fingerprint': PROFILE.contract_fingerprint})
        self.assertNotIn('plan_id', result)
        self.assertEqual(self.transport.states[request.entity_id]['state'], expected_state)
        self.settled()
        return result

    # Positive set and readback, one per helper type and action.

    async def test_input_boolean_on_off(self):
        await self.assert_verified(self.request(action='turn_on'), 'on')
        await self.assert_verified(self.request(action='turn_off'), 'off')
        self.assertEqual(self.dispatches()[0]['data'], {})
        self.assertEqual(self.transport.writes, 2)

    async def test_input_select_live_option(self):
        await self.assert_verified(self.request('input_select.synthetic', 'select_option', 'Night'), 'Night')
        self.assertEqual(self.dispatches(), [{'domain': 'input_select', 'service': 'select_option',
            'entity_id': 'input_select.synthetic', 'data': {'option': 'Night'}, 'wait': False,
            'return_response': False, 'verbose': False}])

    async def test_input_number_in_range_on_step(self):
        await self.assert_verified(self.request('input_number.synthetic', 'set_value', 22.5), '22.5')
        await self.assert_verified(self.request('input_number.synthetic', 'set_value', 30), '30.0')
        self.assertEqual([x['data'] for x in self.dispatches()], [{'value': 22.5}, {'value': 30}])

    async def test_input_text_length_and_pattern(self):
        await self.assert_verified(self.request('input_text.synthetic', 'set_value', 'hello'), 'hello')

    async def test_input_datetime_fields_follow_has_date_and_has_time(self):
        await self.assert_verified(self.request('input_datetime.synthetic', 'set_datetime', '2026-12-24 18:00:00'),
                                   '2026-12-24 18:00:00')
        await self.assert_verified(self.request('input_datetime.alarm', 'set_datetime', '06:15:00'), '06:15:00')
        self.assertEqual([x['data'] for x in self.dispatches()],
                         [{'date': '2026-12-24', 'time': '18:00:00'}, {'time': '06:15:00'}])

    async def test_input_button_press_verifies_new_press_timestamp(self):
        await self.assert_verified(self.request('input_button.synthetic', 'press'),
                                   '2026-09-16T12:00:01.000000+00:00')

    async def test_counter_transitions_and_set_value(self):
        await self.assert_verified(self.request('counter.synthetic', 'increment'), '6')
        await self.assert_verified(self.request('counter.synthetic', 'decrement'), '5')
        await self.assert_verified(self.request('counter.synthetic', 'set_value', 2), '2')
        await self.assert_verified(self.request('counter.synthetic', 'reset'), '0')
        self.assertEqual(self.transport.writes, 4)

    async def test_timer_start_pause_cancel_finish(self):
        await self.assert_verified(self.request('timer.synthetic', 'start', '0:10:00'), 'active')
        self.assertEqual(self.transport.states['timer.synthetic']['attributes']['duration'], '0:10:00')
        await self.assert_verified(self.request('timer.synthetic', 'pause'), 'paused')
        await self.assert_verified(self.request('timer.synthetic', 'start'), 'active')
        await self.assert_verified(self.request('timer.synthetic', 'cancel'), 'idle')
        self.assertEqual(self.transport.states['timer.synthetic']['attributes']['last_transition'], 'cancelled')
        await self.assert_verified(self.request('timer.running', 'finish'), 'idle')
        self.assertEqual(self.transport.states['timer.running']['attributes']['last_transition'], 'finished')
        self.assertEqual([x['data'] for x in self.dispatches()][:3], [{'duration': '0:10:00'}, {}, {}])

    async def test_timer_start_on_running_timer_verifies_restart(self):
        await self.assert_verified(self.request('timer.running', 'start'), 'active')
        self.assertNotEqual(self.transport.states['timer.running']['attributes']['finishes_at'],
                            '2026-09-16T12:05:00+00:00')

    async def test_duration_is_canonicalized_before_dispatch(self):
        await self.assert_verified(self.request('timer.synthetic', 'start', '01:30:00'), 'active')
        self.assertEqual(self.dispatches()[0]['data'], {'duration': '1:30:00'})

    # No-op: value already current, no dispatch; transitions never no-op.

    async def test_value_already_current_is_no_change_without_dispatch(self):
        cases = [self.request(action='turn_off'),
                 self.request('input_select.synthetic', 'select_option', 'Home'),
                 self.request('input_number.synthetic', 'set_value', 20),
                 self.request('input_text.synthetic', 'set_value', 'abc'),
                 self.request('input_datetime.synthetic', 'set_datetime', '2026-09-16 07:30:00'),
                 self.request('counter.synthetic', 'set_value', 5)]
        for request in cases:
            with self.subTest(entity=request.entity_id):
                result = await self.call(request)
                self.assertEqual(result['status'], 'no_change', result)
                self.assertEqual(result['provider_attempt_count'], 0)
                self.assertFalse(result['dispatch_intent_recorded'])
                self.assertEqual(result['safety'], {'evaluated': False, 'reason': 'value_already_current'})
        self.assertEqual(self.transport.writes, 0)
        self.assertEqual(self.reads, [])
        self.assertEqual(request_summary(self.telemetry)['safety_gate'], 'not_evaluated_value_already_current')
        self.settled()

    async def test_counter_reset_at_initial_is_no_change(self):
        self.transport.states['counter.synthetic']['state'] = '0'
        result = await self.call(self.request('counter.synthetic', 'reset'))
        self.assertEqual(result['status'], 'no_change')
        self.assertEqual(self.transport.writes, 0)

    # Empty values are valid when the live configuration allows them (Codex M1).

    def optional_text(self, state, pattern=None):
        self.transport.states['input_text.synthetic'].update(
            state=state, attributes={'min': 0, 'max': 8, 'pattern': pattern, 'mode': 'text'})

    async def test_empty_input_text_baseline_changes_to_value(self):
        self.optional_text('')
        await self.assert_verified(self.request('input_text.synthetic', 'set_value', 'hello'), 'hello')
        self.assertEqual(self.dispatches()[0]['data'], {'value': 'hello'})

    async def test_input_text_cleared_to_empty_is_verified_and_releases_locks(self):
        self.optional_text('abc', pattern='[a-z]*')
        result = await self.assert_verified(self.request('input_text.synthetic', 'set_value', ''), '')
        self.assertEqual(result['provider_attempt_count'], 1)
        self.assertEqual(self.dispatches()[0]['data'], {'value': ''})
        self.assertEqual(self.transport.writes, 1)

    async def test_empty_input_text_already_empty_is_no_change(self):
        self.optional_text('')
        result = await self.call(self.request('input_text.synthetic', 'set_value', ''))
        self.assertEqual(result['status'], 'no_change', result)
        self.assertEqual(result['provider_attempt_count'], 0)
        self.assertFalse(result['dispatch_intent_recorded'])
        self.assertEqual(self.dispatches(), [])
        self.settled()

    async def test_empty_live_select_option_selects_and_reads_back(self):
        self.transport.states['input_select.synthetic']['attributes']['options'] = ['', 'Home', 'Away']
        await self.assert_verified(self.request('input_select.synthetic', 'select_option', ''), '')
        self.assertEqual(self.dispatches()[0]['data'], {'option': ''})
        result = await self.call(self.request('input_select.synthetic', 'select_option', ''))
        self.assertEqual(result['status'], 'no_change', result)
        self.assertEqual(self.transport.writes, 1)
        await self.assert_verified(self.request('input_select.synthetic', 'select_option', 'Away'), 'Away')

    async def test_empty_value_or_state_outside_live_configuration_refuses(self):
        # Empty request values: checked against live options, length and pattern.
        for request, category in [(self.request('input_select.synthetic', 'select_option', ''), 'option_not_available'),
                                  (self.request('input_text.synthetic', 'set_value', ''), 'length_out_of_range')]:
            with self.subTest(request=request.entity_id), self.assertRaisesRegex(HelperRefusal, category):
                await self.call(request)
        # Empty states the domain or live configuration cannot hold, and
        # missing or unknown states the domain cannot hold, stay unprovable.
        cases = [('input_text.synthetic', '', {'min': 1, 'max': 8, 'pattern': None}, 'hello'),
                 ('input_text.synthetic', '', {'min': 0, 'max': 8, 'pattern': '[a-z]+'}, 'hello'),
                 ('input_text.synthetic', '', {'min': 0, 'max': 8, 'pattern': '['}, 'hello'),
                 ('input_text.synthetic', None, {'min': 0, 'max': 8, 'pattern': None}, 'hello'),
                 ('input_select.synthetic', '', {'options': ['Home', 'Away']}, 'Away'),
                 ('input_number.synthetic', '', {'min': 10, 'max': 30, 'step': 0.5}, 22.5),
                 ('input_datetime.synthetic', '', {'has_date': True, 'has_time': True}, '2026-12-24 18:00:00'),
                 ('counter.synthetic', '', {'initial': 0, 'step': 1, 'minimum': 0, 'maximum': 6}, 2),
                 ('input_button.synthetic', '', {}, None),
                 ('input_boolean.synthetic', None, {}, None),
                 ('timer.synthetic', 'unknown', {'duration': '0:05:00'}, None)]
        for entity, state, attributes, value in cases:
            with self.subTest(entity=entity, state=state, attributes=attributes):
                self.transport.states[entity].update(state=state, attributes=attributes)
                action = HELPER_ACTIONS[entity.split('.')[0]][-1 if entity.startswith('counter') else 0]
                with self.assertRaisesRegex(HelperRefusal, 'target_state_unavailable'):
                    await self.call(self.request(entity, action, value))
        self.assertEqual(self.dispatches(), []); self.settled()

    async def test_never_pressed_button_with_unknown_state_can_be_pressed(self):
        self.transport.states['input_button.synthetic']['state'] = 'unknown'
        await self.assert_verified(self.request('input_button.synthetic', 'press'),
                                   '2026-09-16T12:00:01.000000+00:00')

    # The on-step check is bounded to a tiny fraction of ONE step (Codex M2).

    def wide_number(self, low=0, high=2000000000, step=1, state='0.0'):
        self.transport.states['input_number.synthetic'].update(
            state=state, attributes={'min': low, 'max': high, 'step': step})

    async def test_half_step_at_large_magnitude_refuses_without_dispatch(self):
        self.wide_number()
        for value in [1000000000.5, 1999999999.5, 1000000000.25, 1000000000.000001]:
            with self.subTest(value=value), self.assertRaisesRegex(HelperRefusal, 'not_on_step') as caught:
                await self.call(self.request('input_number.synthetic', 'set_value', value))
            self.assertEqual(caught.exception.details['status'], 'refused')
        self.assertEqual(self.dispatches(), []); self.assertEqual(self.transport.writes, 0)
        self.settled()

    async def test_large_on_step_values_are_accepted(self):
        self.wide_number()
        for value in [1000000000, 1999999999, 2000000000, 1000000001.0]:
            await self.assert_verified(self.request('input_number.synthetic', 'set_value', value),
                                       f'{float(value):.15}')
        self.wide_number(step=0.5)
        await self.assert_verified(self.request('input_number.synthetic', 'set_value', 1999999999.5),
                                   '1999999999.5')
        self.assertEqual(self.transport.writes, 5)

    async def test_ordinary_decimal_steps_are_accepted(self):
        self.wide_number(high=10, step=0.1)
        for value in [0.3, 0.7, 0.1 * 3, 9.9]:
            await self.assert_verified(self.request('input_number.synthetic', 'set_value', value),
                                       f'{float(value):.15}')
        self.wide_number(low=-5, high=5, step=0.25, state='0.0')
        await self.assert_verified(self.request('input_number.synthetic', 'set_value', 1.75), '1.75')
        self.wide_number(low=0.05, high=1, step=0.1, state='0.05')
        await self.assert_verified(self.request('input_number.synthetic', 'set_value', 0.35), '0.35')
        for low, step, value in [(0, 0.1, 0.35), (-5, 0.25, 1.8), (0.05, 0.1, 0.3)]:
            self.wide_number(low=low, high=10, step=step, state=f'{float(low):.15}')
            with self.subTest(value=value), self.assertRaisesRegex(HelperRefusal, 'not_on_step'):
                await self.call(self.request('input_number.synthetic', 'set_value', value))
        self.assertEqual(self.transport.writes, 6)

    async def test_transitions_always_dispatch(self):
        # Press, increment and timer actions are never already reached.
        self.transport.states['timer.synthetic'].update(state='active', attributes={
            'duration': '0:05:00', 'finishes_at': '2026-09-16T12:05:00+00:00'})
        for request in [self.request('input_button.synthetic', 'press'),
                        self.request('counter.synthetic', 'increment'),
                        self.request('timer.synthetic', 'start')]:
            result = await self.call(request)
            self.assertEqual(result['status'], 'succeeded_verified', result)
            self.assertEqual(result['provider_attempt_count'], 1)
        self.assertEqual(self.transport.writes, 3)

    # Invalid values and actions refuse before any dispatch.

    async def test_invalid_values_against_live_configuration_refuse(self):
        cases = [
            (self.request('input_select.synthetic', 'select_option', 'Vacation'), 'option_not_available'),
            (self.request('input_select.synthetic', 'select_option', 'home'), 'option_not_available'),
            (self.request('input_number.synthetic', 'set_value', 30.5), 'out_of_range'),
            (self.request('input_number.synthetic', 'set_value', 9), 'out_of_range'),
            (self.request('input_number.synthetic', 'set_value', 20.25), 'not_on_step'),
            (self.request('input_text.synthetic', 'set_value', 'toolongvalue'), 'length_out_of_range'),
            (self.request('input_text.synthetic', 'set_value', ''), 'length_out_of_range'),
            (self.request('input_text.synthetic', 'set_value', 'ABC'), 'pattern_mismatch'),
            (self.request('input_datetime.synthetic', 'set_datetime', '18:00:00'), 'fields_mismatch'),
            (self.request('input_datetime.alarm', 'set_datetime', '2026-12-24'), 'fields_mismatch'),
            (self.request('counter.synthetic', 'increment'), 'counter_at_limit'),
            (self.request('counter.synthetic', 'set_value', 7), 'out_of_range'),
            (self.request('timer.synthetic', 'pause'), 'timer_not_active'),
            (self.request('timer.synthetic', 'cancel'), 'timer_not_running'),
            (self.request('timer.synthetic', 'finish'), 'timer_not_running'),
        ]
        self.transport.states['counter.synthetic']['state'] = '6'  # at maximum: increment would clamp
        for request, category in cases:
            with self.subTest(request=request.entity_id, value=request.value, action=request.action):
                with self.assertRaisesRegex(HelperRefusal, category) as caught:
                    await self.call(request)
                self.assertEqual(caught.exception.details['status'], 'refused')
                self.assertEqual(caught.exception.details['fallback'], 'none')
        self.assertEqual(self.dispatches(), [])
        self.assertEqual(self.reads, [])
        self.settled()

    async def test_counter_step_and_decrement_limit(self):
        self.transport.states['counter.synthetic'].update(state='0', attributes={
            'initial': 0, 'step': 2, 'minimum': 0, 'maximum': 10})
        for request, category in [(self.request('counter.synthetic', 'set_value', 3), 'not_on_step'),
                                  (self.request('counter.synthetic', 'decrement'), 'counter_at_limit')]:
            with self.assertRaisesRegex(HelperRefusal, category):
                await self.call(request)
        self.assertEqual(self.transport.writes, 0)

    async def test_unsupported_or_invalid_pattern_refuses(self):
        for pattern, category in [('[unclosed', 'pattern_unsupported'), ('(', 'pattern_unsupported'),
                                  ('a' * 256, 'attributes_unproven')]:
            self.transport.states['input_text.synthetic']['attributes']['pattern'] = pattern
            with self.assertRaisesRegex(HelperRefusal, category):
                await self.call(self.request('input_text.synthetic', 'set_value', 'abcd'))
        self.assertEqual(self.transport.writes, 0)

    async def test_static_request_refusals(self):
        cases = [('input_boolean.synthetic', 'set_value', 1, 'action_not_allowed'),
                 ('input_button.synthetic', 'turn_on', None, 'action_not_allowed'),
                 ('timer.synthetic', 'increment', None, 'action_not_allowed'),
                 ('input_select.synthetic', 'select_option', None, 'value_required'),
                 ('input_boolean.synthetic', 'turn_on', 'on', 'value_not_allowed'),
                 ('counter.synthetic', 'increment', 1, 'value_not_allowed'),
                 ('input_number.synthetic', 'set_value', 'twenty', 'value_invalid'),
                 ('counter.synthetic', 'set_value', 1.5, 'value_invalid'),
                 ('input_datetime.synthetic', 'set_datetime', '2026-02-30', 'datetime_invalid'),
                 ('input_datetime.synthetic', 'set_datetime', '2026-12-24T18:00:00', 'datetime_invalid'),
                 ('timer.synthetic', 'start', '0:00:00', 'duration_invalid'),
                 ('timer.synthetic', 'start', 600, 'duration_invalid')]
        for entity, action, value, category in cases:
            with self.subTest(entity=entity, action=action), self.assertRaisesRegex(HelperRefusal, category):
                await self.call(self.request(entity, action, value))
        for value in [float('nan'), float('inf')]:
            # JSON transport cannot carry these; the model still refuses them.
            with self.assertRaisesRegex(HelperRefusal, 'value_invalid'):
                self.request('input_number.synthetic', 'set_value', value).checked()
        self.assertEqual(self.transport.calls, [])

    async def test_unavailable_entity_refuses(self):
        for state in ['unavailable', '', 'unknown']:
            self.transport.states['input_boolean.synthetic']['state'] = state
            with self.assertRaisesRegex(HelperRefusal, 'target_state_unavailable'):
                await self.call(self.request())
        for entity in ['input_select.synthetic', 'counter.synthetic', 'timer.synthetic']:
            self.transport.states[entity]['state'] = 'unavailable'
        for request in [self.request('input_select.synthetic', 'select_option', 'Away'),
                        self.request('counter.synthetic', 'increment'), self.request('timer.synthetic', 'start')]:
            with self.assertRaisesRegex(HelperRefusal, 'target_state_unavailable'):
                await self.call(request)
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_missing_or_malformed_live_attributes_refuse(self):
        self.transport.states['input_number.synthetic']['attributes'] = {'min': 10, 'max': 30}
        self.transport.states['input_select.synthetic']['attributes'] = {'options': 'Home,Away'}
        for request in [self.request('input_number.synthetic', 'set_value', 22.5),
                        self.request('input_select.synthetic', 'select_option', 'Away')]:
            with self.assertRaisesRegex(HelperRefusal, 'attributes_unproven'):
                await self.call(request)
        self.assertEqual(self.transport.writes, 0)

    async def test_missing_service_is_provider_unavailable(self):
        self.transport.mode = 'missing_service'
        with self.assertRaisesRegex(HelperRefusal, 'service_unavailable') as caught:
            await self.call(self.request())
        self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(caught.exception.details['status'], 'provider_unavailable')
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_unsigned_core_is_provider_unavailable_without_provider_call(self):
        # Signed-only family: no legacy compiled Core version may substitute.
        unsigned, retired = SignedCore(signed=False), SignedCore()
        retired.allowed = False
        for core in [unsigned, retired]:
            self.core = core
            self.service = self.make_service()
            with self.assertRaisesRegex(HelperRefusal, 'core_authority_unavailable') as caught:
                await self.call(self.request())
            self.assertEqual(caught.exception.details['status'], 'provider_unavailable')
            self.assertTrue(all(PROFILE.capability_id in item for item in core.requirements))
            self.assertFalse(core.leases)
        self.assertEqual(self.transport.calls, [])

    # Safety-critical warning from exact evidence only.

    async def test_guest_mode_requires_confirmation_without_dispatch_then_succeeds_with_ack(self):
        self.evidence['input_boolean.guest_mode'] = GUEST_MODE
        request = self.request('input_boolean.guest_mode', 'turn_on')
        result = await self.call(request)
        self.assertEqual(result['status'], 'confirmation_required', result)
        self.assertEqual(result['dispatch'], 'no_dispatch')
        self.assertFalse(result['task_created'])
        self.assertEqual(result['consequential_automations'], [
            {'automation_id': 'automation.unlock_for_guests', 'name': 'Unlock front door for guests',
             'physical_consequence': 'safety_critical'}])
        self.assertEqual(result['consumers']['known_consumer_count'], 2)
        self.assertEqual(result['consumers']['template_reader_count'], 1)
        self.assertIn('not external approval', result['next_step'])
        self.assertEqual(self.dispatches(), [])
        self.assertIsNone(self.service.load(request.task_id))
        self.assertNotIn('ha_call_service', [tool for tool, _ in self.transport.calls])
        summary = request_summary(self.telemetry)
        self.assertEqual(summary['helper_value_outcome'], 'confirmation_required')
        self.assertEqual(summary['safety_gate'], 'confirmation_required')
        self.assertNotIn('task_id', summary)
        self.settled()

        acknowledged = request.model_copy(update={'acknowledge_safety_critical': True})
        result = await self.call(acknowledged)
        self.assertEqual(result['status'], 'succeeded_verified', result)
        self.assertEqual(result['safety']['confirmation'], CONFIRMATION)
        self.assertFalse(result['safety']['external_approval'])
        self.assertTrue(result['safety']['safety_critical'])
        summary = request_summary(self.telemetry)
        self.assertEqual(summary['safety_gate'], 'acknowledged')
        self.assertEqual(summary['safety_confirmation'], 'model_conveyed_chat_confirmation')
        self.assertIs(summary['external_approval'], False)
        self.assertEqual(self.transport.writes, 1)
        self.assertEqual(self.transport.states['input_boolean.guest_mode']['state'], 'on')
        self.settled()

    async def test_safety_critical_turn_off_also_warns(self):
        self.evidence['input_boolean.guest_mode'] = GUEST_MODE
        self.transport.states['input_boolean.guest_mode']['state'] = 'on'
        result = await self.call(self.request('input_boolean.guest_mode', 'turn_off'))
        self.assertEqual(result['status'], 'confirmation_required')
        self.assertEqual(self.transport.writes, 0)

    async def test_safety_critical_noop_does_not_warn(self):
        self.evidence['input_boolean.guest_mode'] = GUEST_MODE
        result = await self.call(self.request('input_boolean.guest_mode', 'turn_off'))
        self.assertEqual(result['status'], 'no_change')
        self.assertEqual(self.reads, [])

    async def test_engineering_acceptance_opaque_only_never_warns(self):
        self.evidence['input_boolean.engineering_acceptance'] = OPAQUE_ONLY
        for action in ['turn_on', 'turn_off', 'turn_on']:
            result = await self.call(self.request('input_boolean.engineering_acceptance', action))
            self.assertEqual(result['status'], 'succeeded_verified', result)
            self.assertFalse(result['safety']['safety_critical'])
            self.assertEqual(result['safety']['template_reader_count'], 1)
            self.assertEqual(result['safety']['template_readers'],
                             'may read via templates: 1, including Acceptance probe')
            self.assertEqual(result['safety']['known_consumers'], [])
            self.assertEqual(request_summary(self.telemetry)['safety_gate'], 'not_safety_critical')
        self.assertEqual(self.transport.writes, 3); self.settled()

    async def test_referenced_non_critical_consumers_are_information_only(self):
        evidence = deepcopy(GUEST_MODE)
        evidence['binding']['physical_consequence'] = 'routine'
        evidence['binding']['downstream_profiles'][0]['physical_consequence'] = 'routine'
        self.evidence['input_boolean.guest_mode'] = evidence
        result = await self.call(self.request('input_boolean.guest_mode', 'turn_on'))
        self.assertEqual(result['status'], 'succeeded_verified')
        self.assertEqual([x['name'] for x in result['safety']['known_consumers']],
                         ['Unlock front door for guests', 'Guest lights'])
        self.assertIn('2 automation(s) reference this helper', result['safety']['consequence'])

    async def test_evidence_unavailable_proceeds_with_note(self):
        for failure in [UNAVAILABLE, RuntimeError('synthetic-private-index-text')]:
            self.evidence['input_boolean.synthetic'] = failure
            self.transport.states['input_boolean.synthetic']['state'] = 'off'
            result = await self.call(self.request())
            self.assertEqual(result['status'], 'succeeded_verified', result)
            self.assertEqual(result['safety']['evidence'], 'unavailable')
            self.assertIn('evidence was unavailable', result['safety']['note'])
            self.assertNotIn('synthetic-private-index-text', json.dumps(result))
            self.assertEqual(request_summary(self.telemetry)['safety_gate'], 'evidence_unavailable')
        self.assertEqual(self.transport.writes, 2)

    async def test_guest_shaped_call_without_ack_dispatches_when_evidence_unavailable(self):
        # Why installed acceptance never makes a changing guest_mode call (Codex
        # H1): the owner rule proceeds with a note when evidence is unavailable,
        # so an unacknowledged call is not a no-dispatch guarantee.
        self.evidence['input_boolean.guest_mode'] = RuntimeError('synthetic-index-down')
        result = await self.call(self.request('input_boolean.guest_mode', 'turn_on'))
        self.assertEqual(result['status'], 'succeeded_verified', result)
        self.assertEqual(result['safety']['evidence'], 'unavailable')
        self.assertFalse(result['safety']['safety_critical'])
        self.assertEqual(len(self.dispatches()), 1)
        self.assertEqual(self.transport.states['input_boolean.guest_mode']['state'], 'on')

    async def test_binding_withdrawn_during_evidence_read_refuses_before_declaration(self):
        original = self.service.dependency_reader
        async def withdrawing(entity_id):
            self.telemetry.ordinary_helper_value_binding = 'withdrawn'
            return await original(entity_id)
        self.service.dependency_reader = withdrawing
        request = self.request()
        with self.assertRaisesRegex(HelperRefusal, 'authorization'):
            await self.call(request)
        self.assertIsNone(self.service.load(request.task_id))
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_core_retired_during_evidence_read_never_dispatches(self):
        original = self.service.dependency_reader
        async def retiring(entity_id):
            self.core.allowed = False
            return await original(entity_id)
        self.service.dependency_reader = retiring
        result = await self.call(self.request())
        self.assertTrue(result['terminal'])
        self.assertIn(result['status'], {'provider_unavailable', 'refused'})
        self.assertFalse(result['dispatch_intent_recorded'])
        self.assertEqual(self.transport.writes, 0); self.settled()

    # At most once, truthful verification.

    async def test_repeated_request_and_detail_reads_never_mutate(self):
        request = self.request(); first = await self.call(request)
        for _ in range(3):
            again = await self.call(request)
            self.assertEqual(again['status'], 'succeeded_verified')
            self.assertEqual(again['safety'], {'evaluated': False, 'reason': 'reconciliation_of_existing_task'})
            with patch.object(HELPER_VALUE_OPERATIONS, 'service', self.service):
                detail = json.loads(await get_execution_task(request.task_id))
            self.assertIn('succeeded_verified', json.dumps(detail))
        self.assertEqual(first['task_id'], request.task_id)
        self.assertEqual(self.transport.writes, 1); self.settled()

    async def test_changed_arguments_reusing_id_refuse(self):
        request = self.request('input_select.synthetic', 'select_option', 'Away'); await self.call(request)
        for changes in [{'value': 'Night'}, {'entity_id': 'input_select.other'}]:
            with self.assertRaisesRegex(HelperRefusal, 'rebound'):
                await self.call(request.model_copy(update=changes))
        self.assertEqual(self.transport.writes, 1)

    async def test_uncertain_response_never_replays(self):
        for mode in ['timeout', 'malformed']:
            self.transport.mode = mode
            self.transport.states['input_boolean.synthetic']['state'] = 'off'
            request = self.request(); result = await self.call(request)
            self.assertEqual(result['status'], 'succeeded_verified', result)
            self.assertFalse(result['provider_response_received'])
            self.assertNotIn('synthetic-private-provider-text', json.dumps(result))
            await self.call(request); await self.call(request)
        self.assertEqual(self.transport.writes, 2); self.settled()

    async def test_timeout_without_effect_is_reported_unverified_then_failed(self):
        self.transport.mode = 'ack_only'
        request = self.request('counter.synthetic', 'increment')
        result = await self.call(request)
        self.assertFalse(result['terminal'])
        self.assertEqual(result['status'], 'changed_unverified', result)
        again = await self.call(request)
        self.assertEqual(again['status'], 'changed_unverified')
        self.clock.advance(181)
        final = await self.service.reconcile(request.task_id)
        self.assertEqual(final['state'], 'manual_review_required')
        self.assertEqual(final['status'], 'verification_failed', final)
        self.assertEqual(self.transport.writes, 1)

    async def test_wrong_effect_is_not_reported_as_success(self):
        self.transport.wrong_effect = 'Away'
        request = self.request('input_select.synthetic', 'select_option', 'Night')
        result = await self.call(request)
        self.assertNotEqual(result['status'], 'succeeded_verified')
        self.clock.advance(181)
        final = await self.service.reconcile(request.task_id)
        self.assertEqual(final['status'], 'verification_failed')
        self.assertEqual(self.transport.writes, 1)

    async def test_button_without_new_timestamp_is_not_verified(self):
        self.transport.wrong_effect = STAMP
        request = self.request('input_button.synthetic', 'press')
        result = await self.call(request)
        self.assertNotEqual(result['status'], 'succeeded_verified')
        self.assertEqual(self.transport.writes, 1)

    async def test_counter_increment_by_wrong_amount_is_not_verified(self):
        self.transport.wrong_effect = '4'
        result = await self.call(self.request('counter.synthetic', 'increment'))
        self.assertNotEqual(result['status'], 'succeeded_verified')

    async def test_changed_revision_at_dispatch_refuses(self):
        async def change(tool):
            if tool == 'ha_call_service':
                self.transport.states['input_boolean.synthetic']['last_updated'] = 'outside-edit'
        self.transport.before = change
        result = await self.call(self.request())
        self.assertNotEqual(result['status'], 'succeeded_verified')
        self.assertEqual(self.transport.writes, 0); self.settled()

    async def test_duplicate_overlapping_owner_dispatches_once(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        async def block(tool):
            if tool == 'ha_call_service':
                entered.set(); await resume.wait()
        self.transport.before = block; request = self.request()
        pending = asyncio.create_task(self.call(request))
        await entered.wait()
        duplicate = await self.call(request)
        self.assertFalse(duplicate['terminal'])
        resume.set(); result = await pending
        self.assertEqual(result['status'], 'succeeded_verified')
        self.assertEqual(self.transport.writes, 1); self.settled()

    async def test_other_family_binding_cannot_authorize_helper(self):
        request = self.request()
        self.telemetry.ordinary_power_binding = digest(request.model_dump())
        with self.assertRaisesRegex(HelperRefusal, 'authorization'):
            await self.service.control(request)
        self.assertEqual(self.transport.calls, [])

    async def test_password_text_value_is_redacted_in_results(self):
        request = self.request('input_text.secret', 'set_value', 'synthetic-new-secret')
        result = await self.call(request)
        self.assertEqual(result['status'], 'succeeded_verified')
        self.assertEqual(result['value'], 'redacted_password_mode')
        self.assertNotIn('synthetic-new-secret', json.dumps(result))
        self.assertNotIn('synthetic-new-secret', json.dumps(self.service.receipt(request.task_id)))

    async def test_status_mapping_from_durable_facts(self):
        self.assertEqual(status_of({'terminal': True, 'terminal_outcome': 'succeeded_verified',
                                    'dispatch_intent_recorded': False}), 'no_change')
        self.assertEqual(status_of({'terminal': True, 'terminal_outcome': 'provider_unavailable_pre_dispatch',
                                    'dispatch_intent_recorded': False}), 'provider_unavailable')
        self.assertEqual(status_of({'terminal': True, 'terminal_outcome': 'cancelled_pre_dispatch',
                                    'dispatch_intent_recorded': False}), 'refused')
        self.assertEqual(status_of({'terminal': True, 'terminal_outcome': 'dispatch_failed_confirmed',
                                    'dispatch_intent_recorded': True}), 'provider_unavailable')
        self.assertEqual(status_of({'terminal': False, 'dispatch_intent_recorded': False}), 'in_progress')

    async def test_tool_wrapper_returns_bounded_result_and_task_pointer(self):
        request = self.request('counter.synthetic', 'increment')
        self.telemetry.ordinary_helper_value_binding = digest(request.model_dump())
        with patch.object(HELPER_VALUE_OPERATIONS, 'service', self.service):
            result = json.loads(await control_helper(**request.model_dump()))
        self.assertIn('succeeded_verified', json.dumps(result))
        self.assertIn(request.task_id, json.dumps(result))
        self.assertEqual(self.transport.writes, 1)

    async def test_tool_wrapper_confirmation_has_no_task_pointer(self):
        self.evidence['input_boolean.guest_mode'] = GUEST_MODE
        request = self.request('input_boolean.guest_mode', 'turn_on')
        self.telemetry.ordinary_helper_value_binding = digest(request.model_dump())
        with patch.object(HELPER_VALUE_OPERATIONS, 'service', self.service):
            result = json.loads(await control_helper(**request.model_dump()))
        self.assertIn('confirmation_required', json.dumps(result))
        self.assertNotIn(request.task_id, json.dumps(result))
        self.assertEqual(self.transport.writes, 0)


class Helper843Tests(HelperTests):
    version = '8.4.3'


class Helper860Tests(HelperTests):
    version = '8.6.0'


class HelperBoundaryTests(unittest.TestCase):
    valid = dict(entity_id='input_boolean.synthetic', action='turn_on', operation_id='1789552800-' + 'a' * 32)

    def test_arbitrary_service_or_data_cannot_be_passed(self):
        changes = [{'entity_id': x} for x in ['light.synthetic', 'switch.synthetic', 'lock.synthetic',
                                              'script.synthetic', 'automation.synthetic', 'input_boolean.*',
                                              'input_boolean.a,input_boolean.b', ['input_boolean.a'], 'all']]
        changes += [{'action': x} for x in ['toggle', 'reload', 'set_options', 'configure', 'unlock', 'turn_on ']]
        changes += [{'data': {}}, {'service': 'turn_on'}, {'domain': 'lock'}, {'service_data': {}},
                    {'target': {}}, {'area_id': 'x'}, {'device_id': 'x'}, {'wait': True},
                    {'return_response': True}, {'value': {'option': 'x'}}, {'value': ['x']},
                    {'value': True}, {'acknowledge_safety_critical': 'true'}, {'acknowledge_safety_critical': 1}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                HelperRequest.model_validate({**self.valid, **change})

    def test_every_allowed_pair_dispatches_only_its_fixed_service_and_keys(self):
        values = {('input_select', 'select_option'): 'A', ('input_number', 'set_value'): 1,
                  ('input_text', 'set_value'): 'a', ('input_datetime', 'set_datetime'): '2026-01-01 00:00:00',
                  ('counter', 'set_value'): 1, ('timer', 'start'): '0:01:00'}
        pairs = [(domain, action) for domain, actions in HELPER_ACTIONS.items() for action in actions]
        self.assertEqual(set(pairs), set(SERVICE_DATA))
        self.assertEqual(len(pairs), 15)
        for domain, action in pairs:
            request = HelperRequest(entity_id=domain + '.synthetic', action=action, operation_id=self.valid['operation_id'],
                                    value=values.get((domain, action))).checked()
            arguments = request.arguments()
            self.assertEqual((arguments['domain'], arguments['service']), (domain, action))
            self.assertTrue(set(arguments['data']) <= SERVICE_DATA[(domain, action)])
            self.assertEqual(set(arguments), {'domain', 'service', 'entity_id', 'data', 'wait',
                                              'return_response', 'verbose'})
        self.assertEqual(len(ACTIONS), 13)

    def test_helper_task_namespace_is_distinct(self):
        from ha_mcp_engineering.power.contracts import PowerRequest
        operation = self.valid['operation_id']
        self.assertNotEqual(HelperRequest(**self.valid).task_id,
                            PowerRequest(entity_id='light.synthetic', action='turn_on', operation_id=operation).task_id)

    def test_assess_only_exact_evidence_warns(self):
        self.assertTrue(assess(GUEST_MODE)['safety_critical'])
        self.assertFalse(assess(OPAQUE_ONLY)['safety_critical'])
        self.assertFalse(assess(UNAVAILABLE)['safety_critical'])
        # A safety-critical opaque reader cannot lift the binding by itself.
        lifted = deepcopy(OPAQUE_ONLY); lifted['binding']['physical_consequence'] = 'safety_critical'
        self.assertFalse(assess(lifted)['safety_critical'])
        # Zero exact obligations cannot warn even with a referenced profile.
        zero = deepcopy(GUEST_MODE); zero['binding']['exact_dependency_obligation_count'] = 0
        self.assertFalse(assess(zero)['safety_critical'])
        # A referenced critical profile without the binding consequence does not warn.
        unbound = deepcopy(GUEST_MODE); unbound['binding']['physical_consequence'] = 'high'
        self.assertFalse(assess(unbound)['safety_critical'])

    def test_consumer_listing_is_bounded(self):
        evidence = deepcopy(GUEST_MODE)
        evidence['binding']['downstream_profiles'] = [
            {'automation_id': f'automation.a{i}', 'evidence_class': 'referenced', 'physical_consequence': 'safety_critical'}
            for i in range(50)] + [
            {'automation_id': f'automation.t{i}', 'evidence_class': 'opaque', 'physical_consequence': 'routine'}
            for i in range(50)]
        evidence['binding']['relevant_consumer_counts_by_class'] = {'referenced': 50, 'opaque': 50}
        result = assess(evidence)
        self.assertEqual(len(result['known_consumers']), 20)
        self.assertEqual(len(result['consequential_automations']), 20)
        self.assertEqual(result['known_consumer_count'], 50)
        self.assertEqual(result['template_readers'],
                         'may read via templates: 50, including automation.t0, automation.t1, automation.t2')

    def test_provider_authority_is_distinct_and_respects_retained_denial(self):
        from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
        from tests.test_readonly_upstream_gateway import settings
        gateway = UpstreamReadGateway(); gateway.configure(settings())
        for version in ['8.4.3', '8.5.0']:
            self.assertNotEqual(gateway.helper_value_provider_authority_token(version),
                                gateway.power_provider_authority_token(version))
        gateway._readmission_selector = SimpleNamespace(select=lambda **kw: SimpleNamespace(
            authority=SimpleNamespace(decisions=[SimpleNamespace(status=SimpleNamespace(value='deny_only'))])))
        with self.assertRaisesRegex(HelperRefusal, 'denied'):
            gateway.helper_value_provider_authority_token('8.5.0')
        with self.assertRaises(HelperRefusal):
            gateway.helper_value_provider_authority_token('8.5.1')

    def test_descriptor_routing_and_metadata_agree(self):
        from ha_mcp_engineering.tools.registry import get_registered_server
        from ha_mcp_engineering.capabilities import capability_for_tool, CAPABILITY_PROVIDER_MATRIX
        from ha_mcp_engineering.providers.routing import routing_for_tool
        from ha_mcp_engineering.mcp_sdk_compatibility import McpSdkToolRegistry
        tools = McpSdkToolRegistry(get_registered_server()).snapshot()
        tool = tools['control_helper']
        self.assertEqual(set(tool.parameters['properties']),
                         {'entity_id', 'action', 'operation_id', 'value', 'acknowledge_safety_critical'})
        self.assertEqual(set(tool.parameters['properties']['action']['enum']), set(ACTIONS))
        self.assertFalse(tool.annotations.readOnlyHint)
        capability = capability_for_tool('control_helper')
        self.assertEqual(capability['provider'], 'upstream_typed_helper_value')
        self.assertFalse(capability['external_approval_required'])
        self.assertEqual(capability['fallback'], 'none')
        route = routing_for_tool('control_helper')
        self.assertEqual(route.fallback_providers, ())
        matrix = next(x for x in CAPABILITY_PROVIDER_MATRIX if x['tool'] == 'control_helper')
        for release in HELPER_RELEASES.values():
            self.assertIn(release[2], matrix['trust_profile'])
        self.assertEqual(matrix['core_applicability'], 'explicit_signed_core.typed_helper_value_operation_only')

    def test_existing_value_plans_remain_and_are_marked_superseded(self):
        from ha_mcp_engineering.tools.registry import get_registered_server
        from ha_mcp_engineering.mcp_sdk_compatibility import McpSdkToolRegistry
        tools = McpSdkToolRegistry(get_registered_server()).snapshot()
        for name in ['create_helper_state_plan', 'create_input_select_option_plan']:
            self.assertIn('Superseded for value changes by control_helper', tools[name].description)
        self.assertIn('create_helper_options_plan', tools)
        self.assertNotIn('Superseded', tools['create_helper_options_plan'].description)


class HelperAcceptancePlanTests(unittest.TestCase):
    """Installed acceptance never changes the live guest_mode helper (Codex H1).

    Without the acknowledgement a call still dispatches when dependency
    evidence is unavailable, so warning/no-dispatch is proven offline only.
    """
    GUEST = 'input_boolean.guest_mode'
    CHANGING = re.compile(r'calls? `control_helper`|turn_on|turn_off|would change|'
                          r'acknowledge_safety_critical(?:`?\s*[:=]\s*`?true)', re.IGNORECASE)

    def installed(self):
        text = (ROOT/'docs'/'V2_4_2_BETA3_ACCEPTANCE.md').read_text(encoding='utf-8')
        section = text.split('\n## 4. Installed acceptance', 1)[1].split('\n## ', 1)[0]
        return section, re.split(r'\n(?=\d+\. \*\*)', section)[1:]

    def test_no_changing_call_on_live_guest_mode(self):
        section, steps = self.installed()
        self.assertGreaterEqual(len(steps), 5)
        guest_steps = [step for step in steps if self.GUEST in step]
        self.assertTrue(guest_steps)
        for step in guest_steps:
            with self.subTest(step=step.split('\n', 1)[0]):
                self.assertIsNone(self.CHANGING.search(step))
                self.assertNotRegex(step, r'[Ee]xpect[^.]*confirmation_required')
        for step in steps:
            if re.search(r'calls? `control_helper`', step, re.IGNORECASE):
                self.assertEqual(set(re.findall(r'input_boolean\.[a-z0-9_]+', step)),
                                 {'input_boolean.engineering_acceptance'})
        flat = ' '.join(section.split())
        self.assertIn('is **not** an unconditional no-dispatch guarantee', flat)
        self.assertIn('make no changing call on it, with or without `acknowledge_safety_critical`', flat)

    def test_offline_evidence_names_the_synthetic_guest_fixture(self):
        text = ' '.join((ROOT/'docs'/'V2_4_2_BETA3_ACCEPTANCE.md').read_text(encoding='utf-8').split())
        self.assertIn('A synthetic guest_mode-shaped fixture (never the live helper)', text)
        self.assertIn('a call without the acknowledgement is not an unconditional no-dispatch guarantee', text)


class HelperGatewayTests(unittest.TestCase):
    def test_authenticated_gateway_binds_exact_request_and_rejects_extra_arguments(self):
        from tests.test_mcp_inbound_security import settings_for, SECRET
        from tests.same_thread_asgi_client import SameThreadAsgiTestClient
        from ha_mcp_engineering.mcp_server import create_mcp_server
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from ha_mcp_engineering.audit import AuditLogger
        with tempfile.TemporaryDirectory() as directory:
            settings = settings_for(directory); clock, core = Clock(), SignedCore()
            transport = HelperTransport('8.5.0'); audit = AuditLogger(settings.audit_path, 'synthetic-audit-secret')
            evidence = {'input_boolean.guest_mode': GUEST_MODE}
            async def reader(entity_id):
                return deepcopy(evidence.get(entity_id, OPAQUE_ONLY))
            service = HelperService(directory, HelperProvider(transport, lambda *args: transport.authority),
                                    HelperCoreAuthority(core), now=clock, sleep=clock.sleep, audit=audit,
                                    dependency_reader=reader)
            server = create_mcp_server(settings); server.tool()(control_helper)
            inner = server.streamable_http_app()
            gateway = AuthenticatedMcpGateway(inner, settings, audit)
            def body(**args):
                arguments = dict(entity_id='input_boolean.synthetic', action='turn_on',
                                 operation_id=f'{int(clock().timestamp())}-{uuid.uuid4().hex}')
                arguments.update(args)
                return {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                        'params': {'name': 'control_helper', 'arguments': arguments}}
            headers = {'accept': 'application/json, text/event-stream'}
            path = '/' + SECRET + '/mcp'
            with patch.object(HELPER_VALUE_OPERATIONS, 'service', service), SameThreadAsgiTestClient(
                    gateway, lifespan_app=inner, base_url='http://127.0.0.1:8100') as client:
                self.assertEqual(client.post('/synthetic-wrong/mcp', json=body(), headers=headers).status_code, 404)
                for change in [{'entity_id': 'light.synthetic'}, {'data': {}}, {'service': 'toggle'},
                               {'action': 'toggle'}, {'domain': 'lock'}, {'entity_id': ['input_boolean.synthetic']},
                               {'action': 'set_value', 'value': 1}]:
                    self.assertEqual(client.post(path, json=body(**change), headers=headers).status_code, 400, change)
                self.assertEqual(transport.calls, [])
                good = body()
                response = client.post(path, json=good, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertIn('succeeded_verified', response.text)
                repeat = client.post(path, json=good, headers=headers)
                self.assertIn('succeeded_verified', repeat.text)
                guest = body(entity_id='input_boolean.guest_mode')
                warned = client.post(path, json=guest, headers=headers)
                self.assertIn('confirmation_required', warned.text)
                guest['params']['arguments']['acknowledge_safety_critical'] = True
                confirmed = client.post(path, json=guest, headers=headers)
                self.assertIn('succeeded_verified', confirmed.text)
            self.assertEqual(transport.writes, 2)
            rows = [json.loads(line) for line in Path(settings.audit_path).read_text().splitlines()]
            summaries = [r.get('analysis_summary', {}) for r in rows]
            gates = [s.get('safety_gate') for s in summaries if s.get('kind') == 'helper_value' or 'safety_gate' in s]
            self.assertIn('confirmation_required', gates)
            self.assertIn('acknowledged', gates)
            acknowledged = [s for s in summaries if s.get('safety_gate') == 'acknowledged']
            self.assertTrue(all(s['safety_confirmation'] == 'model_conveyed_chat_confirmation'
                                and s['external_approval'] is False for s in acknowledged))
            warned_rows = [s for s in summaries if s.get('helper_value_outcome') == 'confirmation_required']
            self.assertEqual(len(warned_rows), 1)
            self.assertNotIn('task_id', warned_rows[0])
            self.assertTrue(any(r.get('operation_class') == 'typed_helper_value_lifecycle'
                                and r.get('state') == 'succeeded_verified' for r in rows))
            self.assertFalse(core.leases or core.commits)


class HelperSignedAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests.test_core_update_continuity import CoreContinuityTests
        self.fixture = CoreContinuityTests()
        self.fixture.addCleanup = self.addCleanup
        await self.fixture.asyncSetUp()

    async def test_current_signed_registry_does_not_admit_helper_value(self):
        # Dormant until an owner-signed sequence names the new profile.
        runtime, _ = await self.fixture.runtime()
        guard = HelperCoreAuthority(runtime)
        from ha_mcp_engineering.f3.contracts import OperationTarget
        self.assertIsNone(await guard.acquire(SimpleNamespace(
            target=OperationTarget('input_boolean', 'input_boolean.synthetic'))))
        self.assertEqual(runtime.health_snapshot()['issued_lease_count'], 0)



class HelperRealEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """The shipped dependency index and binding builder, over synthetic configs."""

    async def evidence(self, entity_id, configs):
        from ha_mcp_engineering.dependency import DEPENDENCY_ANALYSIS
        from ha_mcp_engineering.dependency.index import DependencyIndex
        from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider
        from ha_mcp_engineering.helper_value.safety import read_helper_dependency_evidence
        from tests.hamcp285_fixtures import InstallationRest, InstallationWebSocket
        rest = InstallationRest(dict(configs), [{'entity_id': entity_id, 'state': 'off', 'attributes': {}}])
        index = DependencyIndex(DirectHaDependencyProvider(rest, InstallationWebSocket([], []), concurrency=4))
        with patch.object(DEPENDENCY_ANALYSIS, 'require', lambda: SimpleNamespace(index=index)):
            return await read_helper_dependency_evidence(entity_id)

    async def test_guest_mode_shaped_exact_lock_consumer_warns(self):
        from tests.hamcp285_fixtures import consumer
        evidence = await self.evidence('input_boolean.guest_mode', {'guest_unlock': consumer(
            'Guest unlock', triggers=[{'trigger': 'state', 'entity_id': 'input_boolean.guest_mode', 'to': 'on'}],
            actions=[{'action': 'lock.unlock', 'target': {'entity_id': 'lock.front_door'}}])})
        self.assertTrue(evidence['available'], evidence)
        self.assertEqual(evidence['binding']['physical_consequence'], 'safety_critical')
        result = assess(evidence)
        self.assertTrue(result['safety_critical'])
        self.assertEqual(result['consequential_automations'], [
            {'automation_id': 'automation.guest_unlock', 'name': 'Guest unlock',
             'physical_consequence': 'safety_critical'}])

    async def test_engineering_acceptance_shaped_template_reader_does_not_warn(self):
        from tests.hamcp285_fixtures import consumer, template
        evidence = await self.evidence('input_boolean.engineering_acceptance', {'dyn_reader': consumer(
            'Dynamic reader',
            conditions=template("{{ states('input_boolean.' ~ states('sensor.which_name')) == 'on' }}"),
            actions=[{'action': 'lock.unlock', 'target': {'entity_id': 'lock.front_door'}}])})
        self.assertTrue(evidence['available'], evidence)
        # The opaque reader's own action is safety-critical; it still never warns.
        self.assertEqual(evidence['binding']['downstream_profiles'][0]['physical_consequence'], 'safety_critical')
        result = assess(evidence)
        self.assertFalse(result['safety_critical'])
        self.assertEqual(result['template_reader_count'], 1)
        self.assertEqual(result['template_readers'], 'may read via templates: 1, including Dynamic reader')

    async def test_index_failure_is_reported_unavailable(self):
        from ha_mcp_engineering.dependency import DEPENDENCY_ANALYSIS
        from ha_mcp_engineering.helper_value.safety import read_helper_dependency_evidence
        def missing():
            raise RuntimeError('synthetic-private-index-text')
        with patch.object(DEPENDENCY_ANALYSIS, 'require', missing):
            evidence = await read_helper_dependency_evidence('input_boolean.guest_mode')
        self.assertEqual(evidence, {'available': False, 'failure_category': 'RuntimeError'})


if __name__ == '__main__':
    unittest.main()
