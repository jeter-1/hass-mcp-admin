"""Independent HAMCP-220 probes; synthetic loopback only."""
import unittest
from test_hamcp220_incident_log_timestamps import TimeBasisServiceTests as Helpers, CORE, SELF, core_text, ms

class ReviewerProbes(unittest.IsolatedAsyncioTestCase):
    setup = Helpers.setup
    read = Helpers.read
    read_all = Helpers.read_all
    add = Helpers.add
    source = staticmethod(Helpers.source)
    labels = staticmethod(Helpers.labels)

    async def test_stale_same_offset_zone_crosses_dst_without_wrong_complete(self):
        await self.setup(supervisor_zone='America/Panama', addon_zone='America/Chicago', now='2026-11-01T10:00:00.000')
        for at, label in [('05:00:00.000', 'older'), ('05:50:00.000', 'anchor'), ('08:10:00.000', 'inside')]:
            self.sim.journal(CORE).add(ms('2026-11-01T'+at), core_text(label))
        page = await self.read(sources=['core'], start='2026-11-01T08:00:00Z', end='2026-11-01T08:30:00Z')
        print('STALE_ZONE', {k:page[k] for k in ('returned','complete','omissions')}, self.source(page,'core')['latest_available'])
        self.assertFalse(page['complete'] and page['returned']==0, 'In-window 08:10Z entry omitted as complete')
        for entry in page['entries']:
            if entry['message'].endswith('inside') and entry['ts'] is not None:
                self.assertEqual(entry['ts'], '2026-11-01T08:10:00.000Z')

    async def test_stale_zone_does_not_emit_confidently_wrong_time(self):
        await self.setup(supervisor_zone='America/Panama', addon_zone='America/Chicago', now='2026-11-01T10:00:00.000')
        for at, label in [('05:00:00.000', 'older'), ('05:50:00.000', 'anchor'), ('08:10:00.000', 'inside')]:
            self.sim.journal(CORE).add(ms('2026-11-01T'+at), core_text(label))
        page = await self.read(sources=['core'], start='2026-11-01T05:30:00Z', end='2026-11-01T10:00:00Z')
        target = [e for e in page['entries'] if e['message'].endswith('inside')]
        print('WRONG_TIMESTAMP', [(e['ts'], e['ts_basis']) for e in target], 'complete',page['complete'])
        for entry in target:
            self.assertIn(entry['ts'], (None,'2026-11-01T08:10:00.000Z'))
        self.assertTrue(target or not page['complete'])

    async def test_dst_merge_order_matches_disclosed_earliest_candidate(self):
        await self.setup(now='2026-11-01T09:00:00.000')
        for path in (CORE, SELF):
            self.sim.journal(path).add(ms('2026-11-01T05:00:00.000'), core_text('older'))
        for at in ('05:45:00.000','06:10:00.000','06:50:00.000','07:10:00.000','07:50:00.000','08:10:00.000'):
            self.sim.journal(CORE).add(ms('2026-11-01T'+at), core_text('core '+at))
        self.sim.journal(SELF).add(ms('2026-11-01T06:30:00.000'), core_text('self middle'))
        pages = await self.read_all(sources=['core','addon:self'],start='2026-11-01T05:30:00Z',end='2026-11-01T08:30:00Z',limit=2)
        keys = [e['ts'] or e['timestamp_uncertain']['candidates'][0] for p in pages for e in p['entries']]
        print('DST_MERGE', keys)
        self.assertEqual(keys, sorted(keys))

    async def test_one_hour_stale_zone_refused(self):
        await self.setup(supervisor_zone='America/New_York', addon_zone='America/Chicago')
        self.add(CORE,'2026-10-22T12:00:00.000',4,1000,'row')
        page=await self.read(sources=['core'],start='2026-10-22T12:00:00Z',end='2026-10-22T12:01:00Z')
        self.assertFalse(page['complete'])
        self.assertEqual(self.source(page,'core')['reason'],'timestamp_basis_mismatch')
        self.assertEqual(page['returned'],0)

    async def test_empty_journal_no_false_entries(self):
        await self.setup()
        page=await self.read(sources=['core'],start='2026-10-22T12:00:00Z',end='2026-10-22T12:01:00Z')
        self.assertEqual(page['returned'],0)
        self.assertEqual(self.source(page,'core')['reason'],'empty_journal')

if __name__=='__main__': unittest.main()
