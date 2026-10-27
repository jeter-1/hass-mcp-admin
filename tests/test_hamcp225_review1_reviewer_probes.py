"""PR315 independent offline probes. All secret-like values are synthetic/runtime-built."""
import asyncio, copy, json, sys, unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path.cwd()/'tests'),str(Path.cwd()/'hass_mcp_engineering_beta'),str(Path.cwd())]
import test_hamcp225_trace_snapshots as a
from ha_mcp_engineering.configuration_definitions import credentials
from ha_mcp_engineering.trace_normalization import sanitize_trace_detail
from ha_mcp_engineering.trace_snapshots.poller import TraceWatchPoller

N='synthetic'+'ProbeNeedle225'
def forms():
 return {
  'split':'pass\tword='+N,
  'percent':'pass%77ord%3D'+N,
  'confusable':'p\u0430ssword='+N,
  'invisible':'pass\u200bword='+N,
  'db_uri':'postgresql'+':'+'//user:'+N+'@example.invalid/db',
 }
def planted(value,position):
 d={**a.header('r1'),'config':{'alias':'Synthetic'},'context':{'id':'ctx'},'trace':{'action/0':[{'path':'action/0','result':{'ok':True}}]}}
 if position=='variables':d['config']['variables']={'message':value}
 if position=='context':d['context']['note']=value
 if position=='changed_variables':d['trace']['action/0'][0]['changed_variables']={'message':value}
 if position=='service_data':d['config']['action']=[{'action':'notify.notify','data':{'message':value}}]
 if position=='error':d['error']=value
 return d

class Security(a.ServiceCase):
 async def test_hardened_screen_applies_before_storage_and_readback(self):
  for form,value in forms().items():
   self.assertTrue(credentials.unverifiable_text(value) or credentials.credential_text(value))
   for pos in ('variables','context','changed_variables','service_data','error'):
    with self.subTest(form=form,position=pos):
     service=a.TraceSnapshotService(self.root/(form+'-'+pos),self.core.command,self.runtime,self.audit,clock=self.clock)
     self.core.runs=[a.header('r1')];self.core.details['r1']=planted(value,pos)
     cap,partial=await service.capture(automation_id='a1',run_id='r1')
     page,_=await service.get(snapshot_id=cap['runs'][0]['snapshot_id'])
     with self.subTest(sink='disk'):
      self.assertFalse(N.encode() in a.all_store_bytes(service.store.root),'synthetic credential persisted')
     with self.subTest(sink='readback'):
      self.assertFalse(N in json.dumps(page),'synthetic credential returned')
     with self.subTest(sink='completeness'):
      self.assertTrue(partial)
 async def test_live_trace_tool_hardened_screen(self):
  for form,value in forms().items():
   for pos in ('variables','context','changed_variables','service_data','error'):
    with self.subTest(form=form,position=pos):
     async def command(payload):return planted(value,pos)
     with patch.object(a.compatibility,'ws_command',command):out=await a.compatibility.get_automation_trace('a1','r1')
     self.assertFalse(N in out,'synthetic credential returned by trace tool')
 async def test_pem_key_and_truncation_boundary(self):
  pem=a.h.private_key_pem(N)
  for label,value in [('key',{pem:'benign'}),('split',{'message':pem.replace('PRIVATE KEY','PRIVATE\nKEY')})]:
   with self.subTest(case=label):
    self.core.runs=[a.header('r1')];self.core.details['r1']={**a.header('r1'),'config':value}
    service=a.TraceSnapshotService(self.root/label,self.core.command,self.runtime,self.audit,clock=self.clock)
    await service.capture(automation_id='a1',run_id='r1')
    self.assertFalse(N.encode() in a.all_store_bytes(service.store.root),'private-key string persisted')
 async def test_index_does_not_restore_sanitized_caller_identifiers(self):
  for field in ('automation_id','run_id'):
   with self.subTest(field=field):
    service=a.TraceSnapshotService(self.root/field,self.core.command,self.runtime,self.audit,known_secrets=(a.KNOWN,),clock=self.clock)
    run=a.KNOWN if field=='run_id' else 'r1';self.core.runs=[a.header(run)]
    cap,_=await service.capture(automation_id=a.KNOWN if field=='automation_id' else 'a1',run_id=run)
    self.assertEqual(cap['runs'][0]['outcome'],'captured')
    self.assertFalse(a.KNOWN.encode() in service.store.index_path.read_bytes(),'known secret persisted in index')
 async def test_clean_snapshot_and_strict_rescan(self):
  self.core.add('r1');cap,partial=await self.service.capture(automation_id='a1',run_id='r1')
  self.assertFalse(partial)
  page,partial=await self.service.get(snapshot_id=cap['runs'][0]['snapshot_id'])
  self.assertFalse(partial);self.assertTrue(page['units'])

class Gateway(a.GatewayStackTests):
 # Inherit only setup/call helpers, suppress duplicated author test discovery below.
 def test_known_identifier_cannot_reach_watch_storage(self):
  rendered,record,wire=self.call('set_automation_trace_watch',{'action':'add','automation_id':a.HA_TOKEN})
  with self.subTest(sink='store'):
   self.assertFalse(a.HA_TOKEN.encode() in a.all_store_bytes(self.root/'store'),'known secret persisted by authenticated watch call')
  with self.subTest(sink='audit'):
   self.assertFalse(a.HA_TOKEN in json.dumps(record),'known secret persisted in production gateway audit')
 def test_watch_limit_failure_is_sanitized(self):
  self.call('set_automation_trace_watch',{'action':'add','automation_id':a.HA_TOKEN})
  for i in range(9):self.call('set_automation_trace_watch',{'action':'add','automation_id':'safe'+str(i)})
  rendered,record,wire=self.call('set_automation_trace_watch',{'action':'add','automation_id':'overflow'})
  self.assertFalse(rendered['success'])
  self.assertFalse(a.HA_TOKEN in wire,'known secret in authenticated refusal response')
 # Don't inherit the author's methods; their baseline is run separately.
for _name in a.GatewayStackTests.__dict__:
 if _name.startswith('test_'):setattr(Gateway,_name,None)

class Polling(a.ServiceCase):
 async def test_every_watch_gets_an_opportunity_under_slow_first_watch(self):
  await self.service.watch(action='add',automation_id='slow',mode='executed')
  await self.service.watch(action='add',automation_id='fast',mode='executed')
  async def baseline(payload):return []
  self.service.command=baseline
  await TraceWatchPoller(self.service,self.runtime).tick()
  visited=[]
  async def slow(payload):
   visited.append(payload['item_id'])
   if payload['type']=='trace/list':
    return [a.header('run'+str(i),minute=i) for i in range(5)]
   if payload['item_id']=='slow':await asyncio.sleep(.04)
   return {**planted('benign','context'),'run_id':payload['run_id']}
  self.service.command=slow
  poller=TraceWatchPoller(self.service,self.runtime,tick_seconds=.1)
  for _ in range(6):
   try:await poller.tick()
   except TimeoutError:pass
  self.assertIn('fast',visited,'first slow watch starves later watch on every cycle')
 async def test_run_after_watch_creation_before_first_tick_is_captured(self):
  await self.service.watch(action='add',automation_id='a1',mode='executed')
  self.core.add('new_run')
  self.core.runs[-1]['timestamp']={'start':a.c.utc_text(self.clock.now+1),'finish':a.c.utc_text(self.clock.now+2)}
  self.clock.now+=30
  await self.service.poll_once(authority=(7,a.VERSION))
  self.assertIn('new_run',[e['run_id'] for e in self.index()['snapshots']],'new watched run silently becomes baseline')
 async def test_minute_ticks_keep_real_run(self):
  await self.service.watch(action='add',automation_id='a1',mode='executed')
  await self.service.poll_once(authority=(7,a.VERSION))
  self.core.add('real',minute=0)
  await self.service.poll_once(authority=(7,a.VERSION))
  for i in range(1,8):
   self.clock.now+=60;self.core.add('condition'+str(i),'failed_conditions',minute=i)
   self.core.runs=self.core.runs[-5:]
   await self.service.poll_once(authority=(7,a.VERSION))
  self.assertIn('real',[e['run_id'] for e in self.index()['snapshots']])
 async def test_authority_loss_after_last_read_stores_nothing(self):
  await self.service.watch(action='add',automation_id='a1',mode='executed')
  poller=TraceWatchPoller(self.service,self.runtime)
  await poller.tick();self.core.add('r1')
  self.core.on_get=lambda payload:setattr(self.runtime,'available',False)
  from ha_mcp_engineering.request_context import current_telemetry
  async def guarded(payload):
   self.assertTrue(current_telemetry().authorize_core_dispatch())
   return await self.core.command(payload)
  self.service.command=guarded
  await poller.tick()
  self.assertEqual(self.index()['snapshots'],[],'stale-authority response committed after retirement')
 async def observation_unknown_gap_not_reported_as_zero_missed_runs(self):
  await self.service.watch(action='add',automation_id='a1',mode='executed')
  await self.service.poll_once(authority=(7,a.VERSION))
  for i in range(6):
   self.clock.now+=60;self.core.add('condition'+str(i),'failed_conditions',minute=i)
   self.core.runs=self.core.runs[-5:]
   await self.service.poll_once(authority=None)
  await self.service.poll_once(authority=(7,a.VERSION))
  result,partial=await self.service.watch(action='list')
  self.assertTrue(partial or result['watches'][0]['uncaptured_runs']!=0,'unknown coverage reported with zero uncaptured runs and complete')

if __name__=='__main__':unittest.main(verbosity=2)
