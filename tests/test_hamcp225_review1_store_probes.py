"""Additional storage and scheduling checks for PR315; synthetic/offline."""
import asyncio,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path.cwd()/'tests'),str(Path.cwd()/'hass_mcp_engineering_beta'),str(Path.cwd())]
import test_hamcp225_trace_snapshots as a

class Storage(a.ServiceCase):
 async def test_concurrent_captures_deduplicate(self):
  self.core.add('r1')
  results=await asyncio.gather(*(self.service.capture(automation_id='a1',run_id='r1') for _ in range(8)))
  self.assertEqual(len(self.index()['snapshots']),1)
  self.assertEqual(sum(v['runs'][0]['outcome']=='captured' for v,p in results),1)
 async def test_byte_capacity_refuses_without_eviction(self):
  self.core.add('r1');self.core.add('r2')
  await self.service.capture(automation_id='a1',run_id='r1')
  before=self.index();used=sum(e['bytes'] for e in before['snapshots'])
  with patch.object(a.c,'STORE_BYTES',used+100):
   value,partial=await self.service.capture(automation_id='a1',run_id='r2')
  self.assertTrue(partial);self.assertEqual(value['runs'][0]['outcome'],'store_full')
  self.assertEqual(before['snapshots'],self.index()['snapshots'])
 async def test_cross_watch_quota_never_evicts_other_watch_or_manual(self):
  self.core.add('manual');await self.service.capture(automation_id='a1',run_id='manual')
  await self.service.watch(action='add',automation_id='a1',mode='executed')
  await self.service.watch(action='add',automation_id='a2',mode='executed')
  await self.service.poll_once(authority=(7,a.VERSION))
  self.core.add('one');await self.service.poll_once(authority=(7,a.VERSION))
  before=self.index();other=[e for e in before['snapshots'] if e['automation_id']=='a2']
  first=next(w for w in before['watches'] if w['automation_id']=='a1')
  self.core.add('two')
  with patch.object(a.c,'WATCH_QUOTA',1):await self.service._poll_watch(first,(7,a.VERSION))
  after=self.index()
  self.assertTrue(all(e in after['snapshots'] for e in other))
  self.assertIn('manual',[e['run_id'] for e in after['snapshots'] if e['origin']=='manual'])
 async def test_restart_after_uncertain_index_commit_is_consistent(self):
  self.core.add('r1')
  def fail(stage):
   if stage=='after_index':raise OSError('synthetic injected disk error')
  self.service.store.fault_hook=fail
  with self.assertRaises(a.c.TraceSnapshotError):await self.service.capture(automation_id='a1',run_id='r1')
  restarted=a.TraceSnapshotService(self.root,self.core.command,self.runtime,self.audit,clock=self.clock)
  listing,_=await restarted.list();self.assertEqual(len(listing['snapshots']),1)
  page,_=await restarted.get(snapshot_id=listing['snapshots'][0]['snapshot_id'])
  self.assertTrue(page['units'])
 async def test_quarantine_obeys_store_retention_and_capacity(self):
  with patch.object(a.c,'MAX_SNAPSHOTS',1):
   for i in range(3):
    run='r'+str(i);self.core.add(run)
    value,_=await self.service.capture(automation_id='a1',run_id=run)
    sid=value['runs'][0]['snapshot_id'];path=self.service.store.records/(sid+'.json')
    path.write_bytes(path.read_bytes().replace(b'Synthetic',b'Tampered!'))
    with self.assertRaises(a.c.TraceSnapshotError):await self.service.get(snapshot_id=sid)
   self.clock.now+=a.c.RETENTION_SECONDS+1
   listing,_=await self.service.list()
   quarantined=list(self.service.store.quarantine_root.glob('*.json'))
   self.assertEqual(listing['store']['bytes'],0)
   self.assertEqual(quarantined,[],'quarantined snapshots outlive retention and bypass store accounting')

if __name__=='__main__':unittest.main(verbosity=2)
