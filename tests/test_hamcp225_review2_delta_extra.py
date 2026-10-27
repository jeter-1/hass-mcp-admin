"""Independent correction probes; synthetic data only, no network."""
import asyncio, json, unittest
from unittest.mock import patch
import test_hamcp225_trace_snapshots as a
import test_hamcp225_cross_path_credentials as cross
from ha_mcp_engineering.configuration_definitions import credentials
from ha_mcp_engineering.trace_normalization import sanitize_trace_detail
from ha_mcp_engineering.trace_snapshots.service import identifier_cleared
from ha_mcp_engineering.trace_snapshots.poller import TraceWatchPoller

class Mode(a.ServiceCase):
 async def test_benign_identifier_preserved_everywhere(self):
  name='rotate_api_token'
  self.assertIsNone(credentials.screen(name))
  result,_=await self.service.watch(action='add',automation_id=name)
  stored=self.index()['watches'][0]['automation_id']
  self.assertEqual(stored,name, 'benign HA identifier was digested by labelled mode')
  self.assertIn(name,json.dumps(result))
 async def test_benign_alias_preserved_in_trace(self):
  self.assertIsNone(credentials.screen('Token refresh'))
  self.core.add('r1')
  self.core.details['r1']={**a.header('r1'),'config':{'alias':'Token refresh'}}
  cap,_=await self.service.capture(automation_id='a1',run_id='r1')
  page,_=await self.service.get(snapshot_id=cap['runs'][0]['snapshot_id'])
  self.assertIn('Token refresh',json.dumps(page))
 async def test_default_mode_still_blocks_real_credentials(self):
  uri='mqtt'+':'+'//user:syntheticModeNeedle@host.invalid'
  for name in (uri,a.KNOWN,a.KNOWN.upper()):
   self.assertIsNotNone(credentials.screen(name,(a.KNOWN,)))
   self.assertFalse(identifier_cleared(name,(a.KNOWN,)))
   self.assertRegex(self.service._persisted(name),r'^withheld_[0-9a-f]{32}$')

class Race(a.ServiceCase):
 async def test_retirement_while_commit_waits_for_service_lock(self):
  await self.service.watch(action='add',automation_id='a1',mode='executed')
  poller=TraceWatchPoller(self.service,self.runtime)
  await poller.tick()
  self.core.add('r1')
  queued=asyncio.Event(); original_run=self.service._run
  async def command(payload):
   result=await self.core.command(payload)
   if payload['type']=='trace/get':
    # Models a concurrent local store operation holding the real service lock.
    await self.service.lock.acquire()
   return result
  async def observe_run(fn,*args,**kwargs):
   if fn==self.service._commit:queued.set()
   return await original_run(fn,*args,**kwargs)
  self.service.command=command
  with patch.object(self.service,'_run',observe_run):
   task=asyncio.create_task(poller.tick())
   try:
    await asyncio.wait_for(queued.wait(),2)
    self.assertTrue(self.service.lock.locked())
    self.assertFalse(task.done())
    self.runtime.available=False
    self.runtime.generation+=1
    self.service.lock.release()
    await task
   finally:
    if self.service.lock.locked():self.service.lock.release()
    if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
  self.assertEqual(self.index()['snapshots'],[], 'snapshot committed after retirement while queued behind service lock')

class ExtraCredentials(a.ServiceCase):
 async def test_new_variants_both_paths_and_shared_screen(self):
  secret='newSynthetic225Needle'
  variants=[
   'p%2561ss%2509word%253D'+secret,
   'm\tq\nt\rt'+':'+'//u:'+secret+'@ho\u00a0st.invalid',
   'p\u0430ssw\u03bfrd='+secret,
   'pass\u2060word='+secret,
   '-----BEGIN PRI\tVATE\nKEY-----\n'+secret+'\n-----END PRIVATE KEY-----',
   'secret is '+secret[:8]+'\t'+secret[8:],
  ]
  # First form of secret used as known secret also checks squashed matching.
  for number,value in enumerate(variants):
   for position in ('variables','variables_key','deep_value','deep_key'):
    run='r'+str(number)+position
    self.core.add(run)
    detail=cross.planted(run,position,value)
    self.core.details[run]=detail
    service=a.TraceSnapshotService(self.root/run,self.core.command,self.runtime,self.audit,
                                  known_secrets=((secret,) if number==5 else ()),clock=self.clock)
    with patch.object(credentials,'screen',wraps=credentials.screen) as screen:
     cap,partial=await service.capture(automation_id='a1',run_id=run)
     self.assertTrue(any(call.args[0]==value for call in screen.call_args_list))
    self.assertTrue(partial)
    self.assertFalse(cross.store_holds(service.store.root,secret))
    page,_=await service.get(snapshot_id=cap['runs'][0]['snapshot_id'])
    self.assertFalse(cross.holds(page,secret))
    async def command(payload):return detail
    with patch.object(a.compatibility,'ws_command',command),patch.object(a.compatibility,'HA_TOKEN',secret if number==5 else 'unrelatedSynthetic'):
     output=json.loads(await a.compatibility.get_automation_trace('a1',run))
    self.assertFalse(cross.holds(output,secret))
    self.assertEqual(output['metadata']['completeness'],'partial')

class Quarantine(a.ServiceCase):
 async def test_actual_twenty_stub_bound_and_expiry(self):
  for i in range(25):
   self.core.add('r'+str(i));self.clock.now+=1
   cap,_=await self.service.capture(automation_id='a1',run_id='r'+str(i))
   sid=cap['runs'][0]['snapshot_id']
   path=self.root/a.c.MODEL/'records'/(sid+'.json')
   path.write_bytes(b'corruptSyntheticPayload')
   await self.refused(self.service.get(snapshot_id=sid))
  index=self.index()
  self.assertEqual(len(index['quarantine']['entries']),20)
  self.assertEqual(index['quarantine']['dropped'],5)
  self.assertEqual(list((self.root/a.c.MODEL/'records').iterdir()),[])
  self.assertNotIn(b'corruptSyntheticPayload',a.all_store_bytes(self.root))
  self.clock.now+=a.c.RETENTION_SECONDS+1
  await self.service.list()
  self.assertEqual(self.index()['quarantine']['entries'],[])

if __name__=='__main__':unittest.main(verbosity=2)
