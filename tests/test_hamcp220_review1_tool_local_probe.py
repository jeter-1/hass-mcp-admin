import unittest
import test_hamcp131_incident_logs as h

class LocalToolTests(h.IncidentLogToolTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.sim.render='local'
        self.sim.zone='America/Chicago'
        h.tool_module.INCIDENT_LOGS.service.zone_source=lambda:'America/Chicago'

if __name__=='__main__':
    unittest.main()
