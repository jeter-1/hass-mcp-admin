"""Offline rehearsal of disposable fault orchestration; not Core compatibility."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests"), str(ROOT / "hass_mcp_engineering_beta")]
from typed_core_container_contract import failure_contract
from core_registry_fixtures import CoreSigner, ProjectedCoreSource, core_entry, NOW
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.fan.service import FanService
from ha_mcp_engineering.fan.authority import FanCoreAuthority
from ha_mcp_engineering.providers.upstream_fan import FanProvider
from ha_mcp_engineering.power.service import PowerService, PowerCoreAuthority
from ha_mcp_engineering.providers.upstream_power import PowerProvider
from ha_mcp_engineering.audit import AuditLogger
from ha_mcp_engineering.request_context import begin_request, end_request
from tests.test_typed_fan import Transport
from tests.test_typed_power import PowerTransport

FAN, SWITCH = "fan.hamcp_contract_fan", "switch.hamcp_contract_switch"


class FixtureFan(Transport):
    def assert_fixed(self, args):
        assert args["entity_id"] == FAN
        super().assert_fixed({**args, "entity_id": "fan.synthetic"})


class FixturePower(PowerTransport):
    async def execute_read(self, tool, args, **kwargs):
        result = await super().execute_read(tool, args, **kwargs)
        if tool == "ha_call_service":
            self.states[SWITCH]["attributes"]["synthetic_service_calls"] += 1
        return result


class ContainerFaultTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_refusal_uncertainty_owner_expiry_and_restoration_use_real_executor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            signer = CoreSigner()
            raw = signer.journal_raw(entries=[core_entry("2026.9.3", typed_operations=True)])
            async def fetch(*args):
                return raw
            registry = CoreReleaseRegistry(enabled=True, public_key=signer.public_key_base64,
                                           cache_path=root / "registry.json", fetcher=fetch, now=lambda: NOW)
            core = CoreRuntime()
            core.configure(object(), release_registry=registry,
                           source=ProjectedCoreSource(registry, "2026.9.3", typed_operations=True))
            await core.reconcile_once("offline_disposable_rehearsal")
            fan_transport, power_transport = FixtureFan(), FixturePower("8.5.0")
            fan_transport.catalog = power_transport.catalog
            fan_transport.state["entity_id"] = FAN
            power_transport.states[SWITCH] = {
                "entity_id": SWITCH, "state": "off", "last_updated": "initial",
                "attributes": {"synthetic_service_calls": 0},
            }
            audit = AuditLogger(str(root / "audit.jsonl"), "synthetic-offline-contract")
            fan = FanService(root, FanProvider(fan_transport, lambda *args: fan_transport.authority), FanCoreAuthority(core), audit=audit)
            power = PowerService(root, PowerProvider(power_transport, lambda *args: power_transport.authority), PowerCoreAuthority(core), audit=audit)
            class Rest:
                async def request(self, method, path, body=None):
                    assert path in ("/states/" + FAN, "/states/" + SWITCH)
                    state = fan_transport.state if path.endswith(FAN) else power_transport.states[SWITCH]
                    if method == "POST":
                        assert body is not None and set(body) == {"state", "attributes"}
                        state.update(deepcopy(body), last_updated=datetime.now(timezone.utc).isoformat())
                    else:
                        assert method == "GET"
                    return deepcopy(state)
            artifacts = {}
            def save(_output, name, value):
                self.assertNotIn(name, artifacts)
                artifacts[name] = json.loads(json.dumps(value))
            def check(value, category):
                self.assertTrue(value, category)
            telemetry, token = begin_request("synthetic-disposable-rehearsal")
            try:
                await failure_contract(fan, power, Rest(), telemetry, "offline", root,
                                       check=check, save=save)
                self.assertEqual(fan_transport.writes, 2)
                self.assertEqual(power_transport.writes, 2)
                self.assertEqual(len(artifacts), 8)
                for family in ("fan", "power"):
                    self.assertEqual(artifacts[f"offline-{family}-stale-refusal.json"]["provider_attempt_count"], 0)
                    self.assertFalse(artifacts[f"offline-{family}-uncertain.json"]["terminal"])
                    self.assertEqual(artifacts[f"offline-{family}-recovered.json"]["state"], "succeeded_verified")
                self.assertEqual(core.health_snapshot()["issued_lease_count"], 0)
                self.assertEqual(core.health_snapshot()["active_commit_count"], 0)
            finally:
                await power.close()
                await fan.close()
                end_request(token)
