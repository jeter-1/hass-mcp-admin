"""Independent PR314 negative regressions; synthetic clients only."""
import asyncio
import hashlib
from copy import deepcopy
import sys
import unittest
from unittest.mock import patch
sys.path[:0]=['tests','hass_mcp_engineering_beta']
import test_hamcp232_script_traces as traces
from tests.hamcp232_helper_options_fixtures import HelperOptionsServiceTestCase
from tests.test_hamcp232_helper_options import TIME_CONSTANT_20, valid_intent, contract, planning
from ha_mcp_engineering.errors import HomeAssistantApiError, GovernanceError
from ha_mcp_engineering.request_context import begin_request,end_request

class TraceReview(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  telemetry,self.token=begin_request();telemetry.caller_id='synthetic_authenticated_caller';telemetry.core_dispatch_authorizer=lambda:True
 async def asyncTearDown(self):end_request(self.token)
 async def read(self,payload,**kw):
  return await traces.st.ScriptTraceService(traces.FakeClient(payload),traces.AdmissionCore(),**kw).get_trace(traces.SCRIPT_ID,traces.RUN_ID)
 async def test_postgresql_value_wholly_withheld(self):
  result=await self.read({'variables':{'nested':[{'value':'postgresql://synthetic:SYNTHprobeQ9X@synthetic.invalid/db'}]}})
  self.assertNotIn('SYNTHprobeQ9X',repr(result))
 async def test_encoded_dictionary_key_withheld(self):
  result=await self.read({'variables':{'api%5fkey=SYNTHprobeQ9X':'safe'}})
  self.assertNotIn('SYNTHprobeQ9X',repr(result))
 async def test_public_tool_wholly_withholds_credentials(self):
  service=traces.st.ScriptTraceService(traces.FakeClient({'variables':{'api%5fkey=SYNTHprobeQ9X':'safe'}}),traces.AdmissionCore())
  with patch.object(traces.st.SCRIPT_TRACES,'service',service):
   rendered=await traces.public.registered_get_tool().run({'script_id':traces.SCRIPT_ID,'run_id':traces.RUN_ID})
  self.assertNotIn('SYNTHprobeQ9X',str(rendered))
 async def test_invisible_text_withheld_whole(self):
  result=await self.read({'variables':{'value':'safe\u200btext'}})
  self.assertIn('[withheld:',result['trace']['variables']['value'])
 async def test_screening_precedes_truncation_beyond_limit(self):
  result=await self.read({'variables':{'value':'x'*2000+' postgresql://synthetic:SYNTHprobeQ9X@host/db'}})
  self.assertIn('[withheld:',result['trace']['variables']['value'])
 async def test_benign_unicode_and_multiline_preserved(self):
  value='Kitchen 20 °C, Küche 😀\nordinary text'
  result=await self.read({'variables':{'value':value}})
  self.assertEqual(result['trace']['variables']['value'],value)
 async def test_known_secret_identifier_withheld(self):
  secret='synthetic_known_identifier'
  result=await traces.st.ScriptTraceService(traces.FakeClient({'safe':True}),traces.AdmissionCore(),known_secrets=(secret,)).get_trace(secret,traces.RUN_ID)
  self.assertNotIn(secret,repr(result))

class FlowReview(HelperOptionsServiceTestCase):
 async def test_lost_submit_response_leaves_no_flow(self):
  self.add_filter();created=await self.approved_options(TIME_CONSTANT_20)
  self.options_rest.submit_failure=HomeAssistantApiError(details={'provider_response_received':False})
  result=await self.apply(created)
  self.assertEqual(result['task_state'],'failed_post_dispatch')
  self.assertEqual(len(self.options_rest.submits()),1)
  self.assertEqual(self.options_rest.flows,{})
 async def test_cancel_after_open_leaves_no_flow(self):
  self.add_filter();created=await self.approved_options(TIME_CONSTANT_20)
  gateway=self.runtime._configuration_adapters[contract.CAPABILITY_IDENTITY].gateway; original=gateway.options_entry
  async def cancel_on_held(*args,**kwargs):
   if self.options_rest.flows:raise asyncio.CancelledError()
   return await original(*args,**kwargs)
  with patch.object(gateway,'options_entry',cancel_on_held):
   try:await self.apply(created)
   except asyncio.CancelledError:pass
  self.assertEqual(self.options_rest.submits(),[])
  self.assertEqual(self.options_rest.flows,{})
 async def test_partial_internal_intent_never_submitted(self):
  self.add_filter(precision=7)
  original=planning.prepare
  async def omit_one_field(*args,**kwargs):
   prepared=await original(*args,**kwargs)
   del prepared.items[0].intent[contract.INTENT_KEY]['submit']['precision']
   return prepared
  with patch.object(planning,'prepare',omit_one_field):
   created=await self.create_options(TIME_CONSTANT_20)
  await self.approve(created)
  result=await self.apply(created)
  self.assertEqual(self.options_rest.visible('filterentry01')['precision'],7,
   'unrequested precision reset by accepted incomplete internal intent')
  self.assertEqual(self.options_rest.submits(),[])
 async def test_retained_unadmitted_task_refuses_before_persistence(self):
  self.add_filter();created=await self.approved_options(TIME_CONSTANT_20)
  plan=self.service._load(created['plan_id'])
  await self.runtime._initialize(plan,self.service.plan_hash(plan))
  before={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
  calls=self.ha_calls();self.core.withdraw()
  try:await self.apply(created)
  except GovernanceError:pass
  self.assertEqual(self.ha_calls(),calls)
  after={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
  changed=sorted(p for p in before.keys()|after.keys() if before.get(p)!=after.get(p))
  self.assertEqual(changed,[])
 async def test_first_apply_without_core_refuses_before_persistence(self):
  self.add_filter();created=await self.approved_options(TIME_CONSTANT_20)
  before={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
  calls=self.ha_calls();self.runtime.core_runtime=None
  with self.assertRaises(GovernanceError):await self.apply(created)
  self.assertEqual(self.ha_calls(),calls)
  after={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
  self.assertEqual(before,after)
 async def test_normal_baseline_aborts_its_flow(self):
  self.add_filter();await self.create_options(TIME_CONSTANT_20)
  self.assertEqual(len(self.options_rest.opens()),1)
  self.assertEqual(len(self.options_rest.aborts()),1)
  self.assertEqual(self.options_rest.flows,{})

class TriggerReview(unittest.IsolatedAsyncioTestCase):
 async def test_disabled_trigger_through_dependency_reader_and_projection(self):
  import test_hamcp145_input_select_option as iso
  from ha_mcp_engineering.governance.input_select_dependency import option_trigger_disclosure
  configs={'season_action':{'alias':'Synthetic disabled trigger','triggers':[{'trigger':'state','entity_id':iso.ENTITY,'to':'Away','enabled':False}],'actions':[{'action':'notify.notify'}]}}
  binding=await iso.InputSelectDependencyReaderTests().assess(configs)
  projected=option_trigger_disclosure(binding,current='Home',option='Away')
  self.assertFalse(any(row['effect']=='fires' for row in projected['option_triggers']))

 def test_disabled_trigger_is_not_disclosed_as_firing(self):
  from ha_mcp_engineering.dependency.state_trigger_values import extract_state_trigger_values,option_effect
  records=extract_state_trigger_values({'triggers':[{'trigger':'state','entity_id':'input_select.season','to':'Winter','enabled':False}]},source_id='season_action',source_entity_id='automation.season_action',secret='')
  self.assertFalse(any(option_effect(row,current='Summer',option='Winter')=='fires' for row in records))

class IntentReview(unittest.TestCase):
 def test_partial_submit_fails_contract_validation(self):
  proposed=valid_intent();proposed[contract.INTENT_KEY]['submit']={'time_constant':20}
  self.assertTrue(contract.intent_errors('filterentry01',proposed))

if __name__=='__main__':unittest.main(verbosity=2)
