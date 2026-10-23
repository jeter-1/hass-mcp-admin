"""Focused H1 follow-up: HA changes zone after /api/config was read."""
import unittest
from test_hamcp220_incident_log_timestamps import TimeBasisServiceTests, ZoneReaderTests, HomeAssistantZoneReader, RecordingCore, CORE, core_text, ms

class ZoneRaceProbe(TimeBasisServiceTests):
    async def test_unchanged_zone_control_returns_the_entry(self):
        await self.setup(supervisor_zone='America/Chicago', addon_zone='America/Chicago', now='2026-11-01T10:00:00.000')
        await ZoneReaderTests.asyncSetUp(self)
        self.service.zone_source = HomeAssistantZoneReader(self.client, RecordingCore())
        for at,label in [('05:00:00.000','older'),('05:50:00.000','anchor'),('08:10:00.000','inside')]:
            self.sim.journal(CORE).add(ms('2026-11-01T'+at), core_text(label))
        result=await self.read(sources=['core'],start='2026-11-01T08:00:00Z',end='2026-11-01T08:30:00Z')
        self.assertTrue(result['complete'])
        self.assertEqual([e['ts'] for e in result['entries']],['2026-11-01T08:10:00.000Z'])

    async def test_zone_change_between_config_and_journal_cannot_claim_empty_complete(self):
        await self.setup(supervisor_zone='America/Chicago', addon_zone='America/Chicago', now='2026-11-01T10:00:00.000')
        # The actual HomeAssistantRestClient talks to a separate loopback /api/config.
        await ZoneReaderTests.asyncSetUp(self)
        core = RecordingCore()
        self.service.zone_source = HomeAssistantZoneReader(self.client, core)
        for at,label in [('05:00:00.000','older'),('05:50:00.000','anchor'),('08:10:00.000','inside')]:
            self.sim.journal(CORE).add(ms('2026-11-01T'+at), core_text(label))
        def zone_changes(sim):
            self.assertEqual(len(self.requests), 1)
            self.assertEqual(self.requests[0][:2], ('GET', '/api/config'))
            self.payload['time_zone'] = self.ha_zone[0] = sim.zone = 'America/Panama'
        self.sim.mutate_before[1] = zone_changes
        result=await self.read(sources=['core'],start='2026-11-01T08:00:00Z',end='2026-11-01T08:30:00Z')
        print('ZONE_RACE', {k:result[k] for k in ('returned','complete','omissions','time_basis')})
        self.assertEqual(core.names(), ['acquire','consume','revalidate','revalidate','finish']*2)
        print('CORE_CONFIG_READS', len(self.requests), 'LEASE', core.names())
        print('LATEST', self.source(result,'core')['latest_available'])
        self.assertFalse(result['complete'] and result['returned']==0, 'Actual 08:10Z entry omitted as complete after zone changed mid-call')

if __name__=='__main__': unittest.main()
