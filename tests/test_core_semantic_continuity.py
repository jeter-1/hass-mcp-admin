"""Signed Core semantics through real dependency planning and governed F3.

Only registry/network data, signing keys and helper I/O are synthetic. The
authority coordinator, transport gates, snapshot, policy and executor are real.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import unittest

from tests import test_beta37_exact_helper_state as helper_fixtures
from tests import test_core_update_continuity as core_fixtures
from tests.test_dependency_build_authority import _Network
from tests.test_ha_core_2026_9_integration import settings
from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime
from ha_mcp_engineering.governance.helper_dependency import HelperDependencyRiskService
from ha_mcp_engineering.governance.helper_dependency import home_assistant_version_admission
from ha_mcp_engineering.errors import HomeAssistantUnavailableError
from ha_mcp_engineering.dependency.index import DependencyFenceError
from ha_mcp_engineering.dependency import semantic_registry as registry_module
from core_registry_fixtures import core_entry, core_revocation


class CoreSemanticContinuityTests(unittest.IsolatedAsyncioTestCase):
    grant = helper_fixtures.ExactHelperStateRuntimeTests.grant

    async def asyncSetUp(self):
        # Reuse the real signed-registry fixture without inheriting its tests.
        self.registry_case = core_fixtures.CoreContinuityTests()
        await self.registry_case.asyncSetUp()
        self.addCleanup(self.registry_case.doCleanups)
        await helper_fixtures.ExactHelperStateRuntimeTests.asyncSetUp(self)
        self.addAsyncCleanup(helper_fixtures.ExactHelperStateRuntimeTests.asyncTearDown, self)
        self.network = _Network()
        self.network.payloads["/states"] = [
            {"entity_id": self.helper.entity_id, "state": "off", "attributes": {}}
        ]
        self.network.payloads["config/label_registry/list"] = []
        network_patch = patch("ha_mcp_engineering.clients.rest.aiohttp.ClientSession",
                              side_effect=self.network.session)
        network_patch.start()
        self.addCleanup(network_patch.stop)

    async def configure(self, version="2026.9.2", *, signed=True):
        self.registry_case.raw = self.registry_case.signer.journal_raw(
            entries=[core_entry(version)] if signed else [])
        self.core, self.source = await self.registry_case.runtime(version)
        self.network.payloads["/config"] = {"version": version}
        self.clock.value = datetime.now(timezone.utc) + timedelta(seconds=1)
        self.runtime.core_runtime = self.core
        self.dependency_runtime = DependencyAnalysisRuntime()
        configured = settings()
        self.dependency_runtime.configure(HomeAssistantRestClient(configured),
                                          HomeAssistantWebSocketClient(configured),
                                          core_runtime=self.core)
        self.addAsyncCleanup(self.dependency_runtime.shutdown)
        self.index = self.dependency_runtime.require().index
        self.risk = HelperDependencyRiskService(self.index)
        self.service.helper_dependency_risk_reader = self.risk.assess
        self.runtime = helper_fixtures.F3RuntimeIntegration(
            service=self.service, storage_root=str(self.service.repository.root),
            configuration_gateway=helper_fixtures.UnusedConfigurationGateway(),
            backup_gateway=None, lifecycle_gateway=None,
            helper_state_gateway=self.helper,
            provider_identity_reader=helper_fixtures.forbidden_upstream_identity,
            retention_days=90, core_runtime=self.core)
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    def assert_settled(self):
        health = self.core.health_snapshot()
        for name in ("issued_lease_count", "active_commit_count", "capacity_exhaustion_count", "fallback_count"):
            self.assertEqual(health[name], 0, name)

    async def test_reviewed_uncompiled_core_plans_and_executes_once(self):
        await self.configure()
        await self.assert_plans_and_executes_once()

    async def assert_plans_and_executes_once(self):
        self.registry_case.assert_admitted(self.core)
        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id, desired_state="on")
        self.assertEqual(created["plan"]["risk"]["level"], "low", created["plan"])
        self.assertEqual(created["plan"]["policy_decision"]["policy_class"], "standard_admin")
        plan = await self.grant(created)
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.helper.dispatch_count, 1)
        self.assertEqual((await self.helper.read_state(self.helper.entity_id))["state"], "on")
        repeated = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertFalse(repeated["redispatch_performed"])
        self.assertEqual(self.helper.dispatch_count, 1)
        self.assert_settled()

    async def test_another_exact_signed_uncompiled_version_executes(self):
        await self.configure("2026.9.3")
        await self.assert_plans_and_executes_once()

    async def test_reviewed_template_consumer_remains_execution_eligible(self):
        await self.configure()
        self.network.payloads["/states"].append({
            "entity_id": "automation.synthetic", "state": "on", "attributes": {"id": "synthetic"}})
        self.network.payloads["/config/automation/config/synthetic"] = {
            "id": "synthetic", "triggers": [{"trigger": "template", "value_template":
                "{{ is_state('input_boolean.beta37_exact_action', 'on') }}"}],
            "actions": [{"action": "notify.notify", "data": {"message": "synthetic"}}]}
        evidence = await self.risk.assess(self.helper.entity_id)
        self.assertTrue(evidence["binding"]["execution_eligible"], evidence["binding"])
        self.assertIn("automation.synthetic", evidence["binding"]["relevant_downstream_object_ids"])
        await self.assert_plans_and_executes_once()

    async def test_exact_signed_contract_with_disclosed_unknown_consequence_executes(self):
        await self.configure()
        self.network.payloads["/states"].append({
            "entity_id": "automation.synthetic", "state": "on", "attributes": {"id": "synthetic"}})
        self.network.payloads["/config/automation/config/synthetic"] = {
            "id": "synthetic", "triggers": [{"trigger": "state", "entity_id": self.helper.entity_id}],
            "actions": [{"action": "synthetic_unknown.perform", "data": {"message": "synthetic"}}]}
        created = await self.service.create_helper_state_plan(entity_id=self.helper.entity_id, desired_state="on")
        self.assertTrue(created["plan"]["validation_results"]["dependency_execution_eligible"])
        self.assertEqual(created["plan"]["policy_decision"]["policy_class"], "elevated_admin")
        self.assertEqual(created["plan"]["policy_decision"]["physical_consequence"], "unknown")
        plan = await self.grant(created)
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.helper.dispatch_count, 1)
        self.assertEqual(self.helper.state, "on")
        self.assert_settled()

    async def test_compiled_pairing_preserves_existing_approval_material(self):
        await self.configure("2026.9.1", signed=False)
        first = await self.risk.assess(self.helper.entity_id)
        snapshot = self.index.snapshot
        admitted = home_assistant_version_admission(snapshot)
        self.assertEqual(admitted, home_assistant_version_admission(replace(snapshot, semantic_evidence=None)))
        self.assertNotIn("reviewed_core_semantics", first["binding"])
        await self.assert_plans_and_executes_once()

    async def test_unsigned_unknown_has_no_planning_or_transport_authority(self):
        await self.configure(signed=False)
        await self.assert_planning_refused()
        self.assertEqual(self.network.calls, [])

    async def test_missing_template_capability_cannot_borrow_dependency_authority(self):
        await self.configure()
        entry = core_entry()
        entry["capabilities"] = [c for c in entry["capabilities"]
                                 if c["capability_id"] != "core.template_semantics"]
        self.registry_case.raw = self.registry_case.next_journal(entries=[entry])
        await self.registry_case.registry.refresh()
        await self.core.reconcile_once("synthetic_partial_review")
        await self.assert_planning_refused()
        self.assertEqual(self.network.calls, [])
        # Failure remains local: an unrelated admitted read retains useful
        # acquire/consume/revalidate/finish authority with no new route.
        authority = self.core.acquire(("core.basic_rest_read",))
        self.assertIsNotNone(authority)
        commits = self.core.consume(authority)
        self.assertTrue(self.core.revalidate(authority, commits))
        self.assertTrue(self.core.finish(commits))
        self.assert_settled()

    async def assert_planning_refused(self):
        created = await self.service.create_helper_state_plan(
            entity_id=self.helper.entity_id, desired_state="on")
        self.assertEqual(created["plan"]["policy_decision"]["policy_class"], "prohibited")
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assert_settled()

    async def test_missing_or_caller_supplied_evidence_cannot_admit_unknown(self):
        await self.configure()
        snapshot, _, _ = await self.index.get()
        for value in (None, True, {"admitted": True}):
            with self.subTest(value=value):
                altered = replace(snapshot, semantic_evidence=value)
                self.assertFalse(home_assistant_version_admission(altered)["admitted"])
        self.assert_settled()

    async def test_mismatched_observed_version_cannot_publish(self):
        await self.configure()
        self.network.payloads["/config"]["version"] = "2026.9.3"
        with self.assertRaises(DependencyFenceError):
            await self.index.get()
        self.assertIsNone(self.index.snapshot)
        await self.assert_planning_refused()

    async def test_changed_contract_cannot_authorize_cached_evidence(self):
        await self.configure()
        snapshot, _, _ = await self.index.get()
        altered = replace(snapshot.semantic_evidence, contract=(("sha256", "0" * 64),))
        self.assertFalse(home_assistant_version_admission(replace(snapshot, semantic_evidence=altered))["admitted"])
        with patch.object(registry_module, "semantic_registry_sha256", return_value="0" * 64):
            self.assertFalse(self.index.active_identity()["current"])
            self.assertFalse(home_assistant_version_admission(snapshot)["admitted"])
            await self.assert_planning_refused()
        self.assert_settled()

    async def test_expiry_rejects_cached_reuse_without_extra_reads(self):
        await self.configure()
        snapshot, _, _ = await self.index.get()
        before = len(self.network.calls)
        self.registry_case.clock += timedelta(days=2)
        self.assertFalse(home_assistant_version_admission(snapshot)["admitted"])
        self.assertFalse(self.index.active_identity()["current"])
        await self.assert_planning_refused()
        self.assertEqual(len(self.network.calls), before)

    async def test_revocation_rejects_cached_reuse(self):
        await self.configure()
        snapshot, _, _ = await self.index.get()
        self.registry_case.raw = self.registry_case.next_journal(entries=[], revocations=[core_revocation()])
        self.assertTrue(await self.registry_case.registry.refresh())
        self.assertFalse(home_assistant_version_admission(snapshot)["admitted"])
        await self.assert_planning_refused()

    async def test_reconfiguration_cannot_reuse_old_generation_evidence(self):
        await self.configure()
        snapshot, _, _ = await self.index.get()
        self.core.configure(object(), source=self.source, release_registry=self.registry_case.registry)
        await self.core.reconcile_once("startup")
        self.registry_case.assert_admitted(self.core)
        self.assertFalse(home_assistant_version_admission(snapshot)["admitted"])
        self.assertFalse(self.index.active_identity()["current"])
        # Same valid contracts under a newly collected generation remain usable.
        replacement, _, _ = await self.index.get()
        self.assertTrue(home_assistant_version_admission(replacement)["admitted"])
        self.assert_settled()

    async def test_retirement_during_collection_stops_next_dispatch(self):
        await self.configure()
        self.network.after_read = lambda _: self.core.request_reconciliation(connection_changed=True)
        with self.assertRaises(HomeAssistantUnavailableError):
            await self.index.get()
        self.assertEqual(self.network.calls, ["/config"])
        self.assertIsNone(self.index.snapshot)
        self.assert_settled()

    async def test_retirement_after_collection_prevents_publication(self):
        await self.configure()
        from ha_mcp_engineering.dependency import index as index_module
        original = index_module.snapshot_fingerprint
        def retire(*args, **kwargs):
            result = original(*args, **kwargs)
            self.core.request_reconciliation(connection_changed=True)
            return result
        with patch.object(index_module, "snapshot_fingerprint", side_effect=retire):
            with self.assertRaises(HomeAssistantUnavailableError):
                await self.index.get()
        self.assertIsNone(self.index.snapshot)
        self.assert_settled()

    async def test_retirement_between_metadata_and_helper_admission_refuses(self):
        await self.configure()
        from ha_mcp_engineering.governance import helper_dependency
        build = helper_dependency.build_helper_dependency_risk_binding
        def retire(*args, **kwargs):
            self.core.request_reconciliation(connection_changed=True)
            return build(*args, **kwargs)
        with patch.object(helper_dependency, "build_helper_dependency_risk_binding", side_effect=retire):
            await self.assert_planning_refused()

    async def test_identical_post_lock_refresh_preserves_stable_approval_fingerprint(self):
        await self.configure()
        first = await self.risk.assess(self.helper.entity_id)
        second = await self.risk.assess(self.helper.entity_id, fenced=True)
        self.assertTrue(second["binding"]["execution_eligible"])
        self.assertGreater(second["binding"]["dependency_index_generation"], first["binding"]["dependency_index_generation"])
        self.assertGreater(second["binding"]["dependency_index_source_epoch"], first["binding"]["dependency_index_source_epoch"])
        self.assertEqual(first["binding"]["evidence_fingerprint"], second["binding"]["evidence_fingerprint"])
        self.assertEqual(first["binding"]["reviewed_core_semantics"], second["binding"]["reviewed_core_semantics"])
        self.assert_settled()

    async def test_prefence_signed_scan_cannot_satisfy_post_lock_refresh(self):
        await self.configure()
        self.network.hold("/config")
        early = asyncio.create_task(self.index.get())
        await asyncio.wait_for(self.network.entered["/config"].wait(), 3)
        fence = self.index.open_source_fence("synthetic_post_lock")
        fenced = asyncio.create_task(self.index.get(min_source_epoch=fence))
        self.network.gates["/config"].set()
        old, new = await asyncio.wait_for(asyncio.gather(early, fenced), 3)
        self.assertLess(old[0].source_epoch, fence)
        self.assertGreaterEqual(new[0].source_epoch, fence)
        self.assertEqual(self.network.calls.count("/config"), 2)
        self.assertTrue(home_assistant_version_admission(new[0])["admitted"])
        self.assert_settled()

    async def test_caller_cancellation_does_not_cancel_shared_signed_build(self):
        await self.configure()
        self.network.hold("/config")
        caller = asyncio.create_task(self.index.get())
        await asyncio.wait_for(self.network.entered["/config"].wait(), 3)
        other = asyncio.create_task(self.index.get())
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.network.gates["/config"].set()
        snapshot, _, _ = await asyncio.wait_for(other, 3)
        self.assertTrue(home_assistant_version_admission(snapshot)["admitted"])
        self.assertEqual(self.network.calls.count("/config"), 1)
        self.assert_settled()
        for telemetry in self.network.telemetry:
            self.assertFalse(telemetry.authorize_core_dispatch())

    async def test_shutdown_cancellation_cannot_publish_or_leak_authority(self):
        await self.configure()
        self.network.hold("/config")
        caller = asyncio.create_task(self.index.get())
        await asyncio.wait_for(self.network.entered["/config"].wait(), 3)
        await self.dependency_runtime.shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertIsNone(self.index.snapshot)
        self.assert_settled()

    async def test_signed_prewarm_soft_refresh_and_hard_expiry(self):
        await self.configure()
        self.assertTrue(await asyncio.wait_for(self.dependency_runtime.start_prewarm(startup_delay_seconds=0), 3))
        old = self.index.snapshot
        # Move only synthetic snapshot age; production TTLs stay unchanged.
        self.index.snapshot = replace(old, built_at_monotonic=old.built_at_monotonic - 601)
        stale, rebuilt, _ = await self.index.get(refresh=False)
        self.assertFalse(rebuilt)
        self.assertEqual(self.index.evidence_metadata(stale)["freshness"], "stale_within_hard_ttl")
        await asyncio.wait_for(asyncio.shield(self.index._build_task), 3)
        fresh = await self.risk.assess(self.helper.entity_id)
        self.assertTrue(fresh["binding"]["execution_eligible"])
        self.assertGreater(self.index.generation, old.generation)
        self.index.snapshot.built_at_monotonic -= 3601
        self.network.errors["/states"] = HomeAssistantUnavailableError()
        self.assertFalse(self.index.active_identity()["valid"])
        await self.assert_planning_refused()

    async def test_expiry_at_final_core_predispatch_await_has_zero_dispatch(self):
        await self.configure()
        created = await self.service.create_helper_state_plan(entity_id=self.helper.entity_id, desired_state="on")
        plan = await self.grant(created)
        reconcile = self.core.reconcile_once
        async def expire(reason):
            await reconcile(reason)
            if reason == "mutation_pre_dispatch":
                self.registry_case.clock += timedelta(days=2)
        with patch.object(self.core, "reconcile_once", side_effect=expire):
            result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertEqual(self.helper.dispatch_count, 0)
        await self.runtime.recover_once("synthetic_terminal_recovery")
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assert_settled()

    async def test_retired_plan_cannot_dispatch_after_fresh_authority(self):
        await self.configure()
        created = await self.service.create_helper_state_plan(entity_id=self.helper.entity_id, desired_state="on")
        plan = await self.grant(created)
        self.core.request_reconciliation(connection_changed=True)
        # A new exact version is independently reviewed, but cannot inherit a
        # plan's old version/semantic evidence fingerprint.
        self.registry_case.raw = self.registry_case.next_journal(entries=[core_entry("2026.9.3")])
        await self.registry_case.registry.refresh()
        self.source.version = "2026.9.3"
        self.network.payloads["/config"]["version"] = "2026.9.3"
        await self.core.reconcile_once("synthetic_version_change")
        self.registry_case.assert_admitted(self.core)
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertEqual(self.helper.dispatch_count, 0)
        self.assert_settled()
