"""Supplementary readback of authentic beta.6 synthetic execution history."""

import asyncio
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from tests import test_f3_runtime_integration as fixtures
from ha_mcp_engineering.governance.configuration_reverification import (
    ConfigurationReverification, TIMING,
)
from ha_mcp_engineering.f3.locks import DurableLockStore, LockConflict
from ha_mcp_engineering.f3.models import LockOwner
from ha_mcp_engineering.governance.reverification_storage import ReceiptStorageError


FIXTURE = Path(__file__).parent / "fixtures" / "configuration_reverification"


class CoreReadAuthority:
    def __init__(self):
        self.available = True
        self.current = True
        self.active = 0
        self.revalidated = 0
        self.authority = SimpleNamespace(core_version="2026.9.3", generation=2,
                                         observation_fingerprint="a" * 64)

    async def reconcile_once(self, reason):
        self.reason = reason

    def acquire(self, profiles, **kwargs):
        self.profiles = profiles
        return self.authority if self.available else None

    def consume(self, authority):
        self.active += 1
        return (authority,)

    def revalidate(self, authority, commits):
        self.revalidated += 1
        return self.current

    def finish(self, commits):
        self.active -= 1
        return True

    def release(self, authority):
        return True


class ConfigurationReverificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.F3ConfigurationActivationTests()
        await self.fixture.asyncSetUp()
        self.root, self.service = self.fixture.root, self.fixture.service
        self.runtime, self.gateway = self.fixture.runtime, self.fixture.gateway
        self.provenance = json.loads((FIXTURE / "beta6-history-provenance.json").read_text())
        for name, digest in self.provenance["record_files_sha256"].items():
            original = FIXTURE / "beta6" / name
            self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), digest)
        shutil.copytree(FIXTURE / "beta6", self.root, dirs_exist_ok=True)
        self.binding = {"task_id": self.provenance["task_id"],
                        "expected_plan_hash": self.provenance["plan_hash"],
                        "request_id": str(uuid.uuid4())}
        self.plan = self.service._load(self.provenance["plan_id"])
        self.gateway.configs.clear()
        self.gateway.calls.clear()
        for op in self.plan.operations:
            kind = op.helper_type or op.resource_type
            value = copy.deepcopy(op.proposed_config)
            value["id"] = op.target_id.split(".", 1)[-1] if kind.startswith("input_") else op.target_id
            self.gateway.configs[(kind, op.target_id)] = value
        self.core = CoreReadAuthority()
        self.runtime.core_runtime = self.core
        self.reverification = self.runtime.configuration_reverification
        self.original = self._original_records()

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    def _original_records(self):
        return {name: (self.root / name).read_bytes()
                for name in self.provenance["record_files_sha256"]
                if name.endswith(".json") and "operation-locks" not in name}

    async def run_check(self, **changes):
        return await self.service.reverify_configuration_task(**{**self.binding, **changes})

    def assert_unchanged(self):
        self.assertEqual(self.original, self._original_records())
        self.assertFalse(any(c[0] == "write" for c in self.gateway.calls))
        self.assertEqual(self.core.active, 0)
        self.assertEqual(self.runtime.locks.records(), ())
        task = self.service._load_task(self.binding["task_id"])
        self.assertEqual(task.state.value, "manual_review_required")
        self.assertEqual([self.runtime.children.get(d["child_id"]).dispatch_count
                          for d in self.runtime.children.declarations_for_task(task.task_id)], [1, 1])

    async def test_exact_old_writer_failure_reverified_replayed_and_read_after_restart(self):
        result = await self.run_check()
        self.assertEqual(result["status"], "current_configuration_verified", result)
        self.assertTrue(result["review_resolved"])
        self.assertTrue(result["receipt_persisted"])
        self.assertEqual(len(result["operations"]), 2)
        self.assertEqual([c[0] for c in self.gateway.calls], ["read", "read", "validate_all"])
        self.assertGreaterEqual(self.core.revalidated, 4)
        self.assertIn("core.f3_mutation_verification", self.core.profiles)
        self.assertIn("core.governed_configuration_operation", self.core.profiles)
        public = self.service.get_execution_task(self.binding["task_id"])
        self.assertEqual(public["state"], "manual_review_required")
        self.assertTrue(public["review_resolution"]["review_resolved"])
        saved_calls = list(self.gateway.calls)
        self.runtime.configuration_reverification = ConfigurationReverification(self.runtime, str(self.root / "plans"))
        self.core.available = False
        replay = await self.run_check()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["receipt_hash"], result["receipt_hash"])
        self.assertEqual(self.gateway.calls, saved_calls)
        self.assert_unchanged()

    async def test_each_object_matters_and_later_failure_replaces_latest_projection(self):
        self.assertTrue((await self.run_check())["review_resolved"])
        for key in list(self.gateway.configs):
            with self.subTest(resource=key):
                original = self.gateway.configs[key]
                self.gateway.configs[key] = {**original, "id": "different_identity"}
                result = await self.run_check(request_id=str(uuid.uuid4()))
                self.assertEqual(result["status"], "configuration_mismatch")
                self.assertFalse(result["review_resolved"])
                self.assertFalse(self.service.get_execution_task(self.binding["task_id"])["review_resolution"]["review_resolved"])
                self.gateway.configs[key] = original
        self.assert_unchanged()

    async def test_changed_trigger_guard_and_removed_helper_fail(self):
        automation = next(k for k in self.gateway.configs if k[0] == "automation")
        self.gateway.configs[automation]["actions"][0]["default"][0]["id"] = ["changed"]
        helper = next(k for k in self.gateway.configs if k[0] == "input_boolean")
        del self.gateway.configs[helper]
        result = await self.run_check()
        self.assertEqual(result["status"], "configuration_mismatch", result)
        self.assertFalse(result["operations"][0]["identity_match"])
        self.assertFalse(result["operations"][1]["semantic_match"])
        self.assert_unchanged()

    async def test_fresh_core_authority_required_and_drift_retains_partial_rows(self):
        self.core.available = False
        refused = await self.run_check()
        self.assertEqual(refused["status"], "authority_unavailable", refused)
        self.assertIsNone(refused["provider"])
        self.assertEqual(refused["object_read_attempts"], 0)
        self.assertEqual(self.gateway.calls, [])
        self.core.available = True
        read = self.gateway.read
        async def drift(*args):
            value = await read(*args)
            self.core.current = False
            return value
        self.gateway.read = drift
        result = await self.run_check(request_id=str(uuid.uuid4()))
        self.assertEqual(result["status"], "authority_changed", result)
        self.assertEqual(len(result["operations"]), 1)
        self.assert_unchanged()

    async def test_current_history_binding_wrong_hash_invalid_arguments_and_uuid_reuse(self):
        result = await self.run_check(expected_plan_hash="b" * 64)
        self.assertEqual(result["status"], "unsupported_history")
        self.assertEqual(self.gateway.calls, [])
        result = await self.run_check(request_id="not-a-uuid-secret-fixture")
        self.assertEqual(result["status"], "invalid_request")
        self.assertNotIn("secret-fixture", json.dumps(result))
        original = await self.run_check()
        calls = list(self.gateway.calls)
        reused = await self.run_check(task_id="a" * 32)
        self.assertEqual(reused["status"], "request_binding_mismatch")
        self.assertEqual(self.gateway.calls, calls)
        self.assertTrue(original["review_resolved"])
        self.assert_unchanged()

    async def test_foreign_lock_refuses_without_release_or_provider_call(self):
        _, _, _, requests, _ = await self.reverification._history(self.binding)
        owner = LockOwner("other-owner", "other-task", "other-plan", "other-operation", "other-attempt")
        held = self.runtime.locks.acquire_once(requests, owner=owner, timing=TIMING, now=self.service.now())
        before = self.runtime.locks.records()
        result = await self.run_check()
        self.assertEqual(result["status"], "concurrent_change", result)
        self.assertEqual(self.runtime.locks.records(), before)
        self.assertEqual(self.gateway.calls, [])
        self.runtime.locks.release(held)
        self.assert_unchanged()

    async def test_cancelled_read_is_not_success_and_same_request_never_retries(self):
        started = asyncio.Event()
        async def blocking(*args):
            self.gateway.calls.append(("blocked_read",))
            started.set()
            await asyncio.Event().wait()
        self.gateway.read = blocking
        active = asyncio.create_task(self.run_check())
        await asyncio.wait_for(started.wait(), 2)
        competing = await self.run_check(request_id=str(uuid.uuid4()))
        self.assertEqual(competing["status"], "reverification_in_progress")
        active.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await active
        self.assertFalse(self.service.get_execution_task(self.binding["task_id"])["review_resolution"]["review_resolved"])
        calls = list(self.gateway.calls)
        result = await self.run_check()
        self.assertEqual(result["status"], "interrupted_read")
        self.assertTrue(result["replayed"])
        self.assertEqual(self.gateway.calls, calls)
        self.assert_unchanged()

    async def test_interrupted_read_owner_cleanup_is_exact_and_does_not_rewrite_history(self):
        _, plan, _, requests, history_hash = await self.reverification._history(self.binding)
        start = {"binding": self.binding, "plan_id": plan.plan_id,
                 "history_hash": history_hash, "started_at": self.service.now().isoformat(),
                 "lock_requests": [asdict(r) for r in requests]}
        with self.reverification.store.transaction() as tx:
            tx.append("started", start)
            store = DurableLockStore(str(self.root / "plans"))
            store.acquire_once(requests, owner=self.reverification._owner(self.binding, plan.plan_id),
                               timing=TIMING, now=self.service.now())
        result = await self.run_check()
        self.assertEqual(result["status"], "interrupted_read", result)
        self.assertEqual(self.gateway.calls, [])
        self.assert_unchanged()

    async def test_audit_or_final_persistence_failure_cannot_resolve_review(self):
        with patch.object(self.service.audit, "write", return_value=False):
            result = await self.run_check()
        self.assertEqual(result["status"], "audit_unavailable")
        self.assertFalse(self.service.get_execution_task(self.binding["task_id"])["review_resolution"]["review_resolved"])
        self.assertEqual((await self.run_check())["status"], "interrupted_read")
        def fail_final(stage):
            if stage == "before_replace" and len(self.reverification.store.read()) % 2:
                raise OSError("SYNTHETIC_STORAGE_SECRET_MUST_NOT_ESCAPE")
        self.reverification.store.fault_hook = fail_final
        result = await self.run_check(request_id=str(uuid.uuid4()))
        self.assertEqual(result["status"], "evidence_persistence_failed", result)
        self.assertNotIn("SYNTHETIC_STORAGE_SECRET", json.dumps(result))
        self.assertFalse(self.service.get_execution_task(self.binding["task_id"])["review_resolution"]["review_resolved"])
        self.reverification.store.fault_hook = None
        self.assert_unchanged()

    async def test_provider_failure_and_bad_configuration_check_are_bounded(self):
        self.gateway.validation_result = {"result": "invalid", "errors": "SYNTHETIC_PRIVATE_BODY"}
        result = await self.run_check()
        self.assertEqual(result["status"], "configuration_mismatch", result)
        self.assertNotIn("SYNTHETIC_PRIVATE_BODY", json.dumps(result))
        async def failed(*args):
            raise RuntimeError("SYNTHETIC_PROVIDER_SECRET")
        self.gateway.read = failed
        result = await self.run_check(request_id=str(uuid.uuid4()))
        self.assertEqual(result["status"], "read_unavailable", result)
        self.assertNotIn("SYNTHETIC_PROVIDER_SECRET", json.dumps(result))
        self.assert_unchanged()

    async def test_tampered_history_refuses_and_does_not_emit_configurations(self):
        path = next((self.root / "plans" / "f3-child-execution-v1").glob("*.child.json"))
        value = json.loads(path.read_text())
        value["execution"]["dispatch_count"] = 2
        path.write_text(json.dumps(value))
        result = await self.run_check()
        self.assertFalse(result["review_resolved"])
        self.assertEqual(self.gateway.calls, [])
        self.assertNotIn("proposed_config", json.dumps(result))

    async def test_original_expired_approval_is_history_not_new_write_authority(self):
        from datetime import timedelta
        original_now = self.service.now()
        with patch.object(self.service, "now", return_value=original_now + timedelta(days=365)):
            result = await self.run_check()
        self.assertTrue(result["review_resolved"], result)
        self.assert_unchanged()

    async def test_history_drift_during_read_refuses_receipt_resolution(self):
        original = self.reverification._history
        count = 0
        async def drifting(binding):
            nonlocal count
            result = await original(binding)
            count += 1
            return (*result[:-1], "0" * 64) if count == 2 else result
        with patch.object(self.reverification, "_history", side_effect=drifting):
            result = await self.run_check()
        self.assertEqual(result["status"], "concurrent_change")
        self.assertEqual(len(result["operations"]), 2)
        self.assertFalse(result["review_resolved"])
        self.assert_unchanged()

    async def test_cleanup_failure_and_timeout_remain_unresolved_without_repeated_reads(self):
        from ha_mcp_engineering.governance import configuration_reverification as module
        async def hung(*args):
            await asyncio.Event().wait()
        with patch.object(self.gateway, "read", side_effect=hung), patch.object(module, "DEADLINE_SECONDS", 0.02):
            result = await self.run_check()
        self.assertEqual(result["status"], "read_timeout")
        replay = await self.run_check()
        self.assertEqual(replay["status"], "interrupted_read")
        self.assert_unchanged()
        def failed_finish(commits):
            self.core.active -= 1
            raise RuntimeError("SYNTHETIC_PRIVATE_CLEANUP")
        with patch.object(self.core, "finish", side_effect=failed_finish):
            result = await self.run_check(request_id=str(uuid.uuid4()))
        self.assertEqual(result["status"], "authority_cleanup_failed")
        self.assertEqual(len(result["operations"]), 2)
        self.assertNotIn("SYNTHETIC_PRIVATE_CLEANUP", json.dumps(result))
        self.assert_unchanged()

    async def test_disabled_audit_and_receipt_capacity_issue_no_reads(self):
        from ha_mcp_engineering.governance import configuration_reverification as module
        with patch.object(self.service.audit, "enabled", False):
            result = await self.run_check()
        self.assertEqual(result["status"], "read_authority_or_audit_unavailable")
        with patch.object(module, "MAX_EVENTS", 1):
            result = await self.run_check()
        self.assertEqual(result["status"], "receipt_capacity_exceeded")
        self.assertEqual(self.gateway.calls, [])
        self.assert_unchanged()

    async def test_live_read_owner_loss_cannot_resolve_or_remove_foreign_lock(self):
        # The reader may not claim success if its own lock cleanup cannot be
        # proved. The durable start stays pending for explicit recovery.
        with patch.object(self.reverification, "_cleanup", side_effect=RuntimeError("synthetic lock failure")):
            result = await self.run_check()
        self.assertFalse(result["review_resolved"])
        self.assertTrue(self.runtime.locks.records())
        self.assertFalse(self.service.get_execution_task(self.binding["task_id"])["review_resolution"]["review_resolved"])
        result = await self.run_check()
        self.assertEqual(result["status"], "interrupted_read")
        self.assert_unchanged()

    async def test_tool_positive_and_strict_arguments_never_reflect_private_input(self):
        from ha_mcp_engineering.tools import get_registered_server, registered_tools, governance
        from ha_mcp_engineering.providers.routing import routing_for_tool
        from ha_mcp_engineering.capabilities import BETA_NATIVE_CAPABILITIES
        tool = registered_tools(get_registered_server())["reverify_configuration_task"]
        self.assertEqual(set(tool.parameters["properties"]), set(self.binding))
        self.assertFalse(tool.parameters["additionalProperties"])
        self.assertFalse(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertTrue(tool.annotations.idempotentHint)
        self.assertFalse(tool.annotations.openWorldHint)
        self.assertEqual(routing_for_tool(tool.name).preferred_provider, "engineering")
        capability = next(c for c in BETA_NATIVE_CAPABILITIES if c["tool"] == tool.name)
        self.assertEqual(capability["home_assistant_access"], "read_and_validate")
        self.assertFalse(capability["dispatch_allowed"])
        with patch.object(governance.GOVERNANCE, "require", return_value=self.service):
            for changes in ({"task_id": 10}, {"request_id": True},
                            {"expected_plan_hash": "SYNTHETIC_PRIVATE_SECRET"},
                            {"force": True}, {"provider": "SYNTHETIC_PRIVATE_SECRET"}):
                result = json.loads(await tool.run({**self.binding, **changes}))
                self.assertFalse(result["success"])
                self.assertNotIn("SYNTHETIC_PRIVATE_SECRET", json.dumps(result))
                self.assertEqual(self.gateway.calls, [])
            result = json.loads(await tool.run(self.binding))
        self.assertTrue(result["success"], result)
        self.assertTrue(result["data"]["review_resolved"], result)
        self.assertEqual(len(self.gateway.calls), 3)
        self.assert_unchanged()

    async def test_approval_grant_and_consumption_witnesses_must_agree(self):
        plan_path = self.root / 'plans' / (self.plan.plan_id + '.json')
        task_path = next((self.root / 'plans' / 'execution-tasks-v1').glob('*.json'))
        child_path = next((self.root / 'plans' / 'f3-child-execution-v1').glob('*.child.json'))
        cases = [
            (plan_path, ('approval', 'approver_principal'), 'different_synthetic_principal'),
            (plan_path, ('approval', 'consumed_at'), '2026-07-24T12:00:00+00:00'),
            (plan_path, ('apply_request_id',), 'different_synthetic_request'),
            (task_path, ('approval_reference', 'plan_approval', 'consumed_at'), None),
            (child_path, ('runtime', 'approval_consumption_reference', 'plan_hash'), 'b' * 64),
            (child_path, ('runtime', 'approval_consumption_reference', 'policy_decision_hash'), 'b' * 64),
            (child_path, ('runtime', 'approval_consumption_reference', 'public_task_id'), 'b' * 32),
            (child_path, ('runtime', 'approval_consumption_reference', 'sequence_hash'), 'b' * 64),
            (child_path, ('runtime', 'approval_consumption_reference', 'consumed_at'), None),
            (child_path, ('runtime', 'approval_consumption_reference'), None),
            (child_path, ('declaration', 'approval_bundle_hash'), 'b' * 64),
        ]
        for path, keys, changed in cases:
            original = path.read_bytes()
            with self.subTest(keys=keys):
                try:
                    value = json.loads(original)
                    target = value
                    for key in keys[:-1]:
                        target = target[key]
                    target[keys[-1]] = changed
                    path.write_text(json.dumps(value))
                    result = await self.run_check(request_id=str(uuid.uuid4()))
                    self.assertFalse(result['review_resolved'], result)
                    self.assertEqual(self.gateway.calls, [])
                finally:
                    path.write_bytes(original)
        self.assertTrue((await self.run_check())['review_resolved'])
        self.assert_unchanged()

    async def test_consumption_witness_drift_during_reads_preserves_partial_evidence(self):
        child = next((self.root / 'plans' / 'f3-child-execution-v1').glob('*.child.json'))
        original = child.read_bytes()
        read = self.gateway.read
        async def drift(*args):
            result = await read(*args)
            data = json.loads(original)
            data['runtime']['approval_consumption_reference']['consumed_at'] = '2026-07-24T12:00:00+00:00'
            child.write_text(json.dumps(data))
            return result
        try:
            with patch.object(self.gateway, 'read', side_effect=drift):
                result = await self.run_check()
            self.assertEqual(result['status'], 'integrity_failure', result)
            self.assertEqual(len(result['operations']), 2)
            self.assertFalse(result['review_resolved'])
        finally:
            child.write_bytes(original)
        self.assert_unchanged()

    async def test_fresh_preflight_refusals_supersede_success_durably_without_reads(self):
        from contextlib import ExitStack
        plan_path = self.root / 'plans' / (self.plan.plan_id + '.json')
        plan_bytes = plan_path.read_bytes()
        for cause in ('audit', 'core', 'legacy_conflict', 'original_lock', 'source'):
            with self.subTest(cause=cause):
                accepted_id = str(uuid.uuid4())
                self.assertTrue((await self.run_check(request_id=accepted_id))['review_resolved'])
                calls = list(self.gateway.calls)
                held = None
                try:
                    with ExitStack() as context:
                        if cause == 'audit':
                            context.enter_context(patch.object(self.service.audit, 'enabled', False))
                        elif cause == 'core':
                            context.enter_context(patch.object(self.runtime, 'core_runtime', None))
                        elif cause == 'legacy_conflict':
                            context.enter_context(patch.object(self.runtime, '_has_active_legacy_conflict', return_value=True))
                        elif cause == 'original_lock':
                            task, _, _, requests, _ = await self.reverification._history(self.binding)
                            owner = LockOwner('original-owner', task.legacy_projection['child_execution_ids'][0],
                                              self.plan.plan_id, 'synthetic', 'synthetic-attempt')
                            held = self.runtime.locks.acquire_once(requests, owner=owner, timing=TIMING, now=self.service.now())
                        else:
                            data = json.loads(plan_bytes)
                            data['approval']['approver_principal'] = 'different_synthetic_principal'
                            plan_path.write_text(json.dumps(data))
                        refused_id = str(uuid.uuid4())
                        result = await self.run_check(request_id=refused_id)
                        self.assertFalse(result['review_resolved'], result)
                        self.assertTrue(result['receipt_persisted'], result)
                        if cause == 'audit':
                            self.assertIsNone(result['audit_event_id'])
                    self.assertEqual(self.gateway.calls, calls)
                    self.runtime.configuration_reverification = ConfigurationReverification(self.runtime, str(self.root / 'plans'))
                    self.reverification = self.runtime.configuration_reverification
                    projected = self.service.get_execution_task(self.binding['task_id'])['review_resolution']
                    self.assertFalse(projected['review_resolved'], projected)
                    self.assertEqual(projected['request_id'], refused_id)
                    replay = await self.run_check(request_id=accepted_id)
                    self.assertTrue(replay['review_resolved'])
                    self.assertTrue(replay['replayed'])
                    self.assertEqual(self.gateway.calls, calls)
                    self.assertFalse(self.service.get_execution_task(self.binding['task_id'])['review_resolution']['review_resolved'])
                finally:
                    plan_path.write_bytes(plan_bytes)
                    if held is not None:
                        self.runtime.locks.release(held)
        self.assert_unchanged()

    async def test_bad_binding_cannot_supersede_dated_success(self):
        self.assertTrue((await self.run_check())['review_resolved'])
        calls = list(self.gateway.calls)
        for change in ({'expected_plan_hash': 'b' * 64}, {'request_id': 'invalid'}, {'task_id': 'a' * 32}):
            result = await self.run_check(**{'request_id': str(uuid.uuid4()), **change})
            self.assertFalse(result['review_resolved'])
            self.assertTrue(self.service.get_execution_task(self.binding['task_id'])['review_resolution']['review_resolved'])
        self.assertEqual(self.gateway.calls, calls)
        self.assert_unchanged()

    async def test_unpersisted_fresh_refusal_reports_uncertainty(self):
        self.assertTrue((await self.run_check())['review_resolved'])
        original = self.reverification.store.path.read_bytes()
        def fail(stage):
            if stage == 'before_replace':
                raise OSError('SYNTHETIC_PRIVATE_STORAGE_ERROR')
        self.reverification.store.fault_hook = fail
        with patch.object(self.service.audit, 'enabled', False):
            result = await self.run_check(request_id=str(uuid.uuid4()))
        self.assertEqual(result['status'], 'evidence_persistence_failed')
        self.assertTrue(result['evidence_uncertain'])
        self.assertFalse(result['receipt_persisted'])
        self.assertFalse(self.service.get_execution_task(self.binding['task_id'])['review_resolution']['review_resolved'])
        self.assertEqual(original, self.reverification.store.path.read_bytes())
        self.reverification.store.fault_hook = None
        self.assert_unchanged()

    async def test_receipt_read_damage_and_io_errors_have_fixed_distinct_outcomes(self):
        self.assertTrue((await self.run_check())['review_resolved'])
        store = self.reverification.store
        original = store.path.read_bytes()
        calls = list(self.gateway.calls)
        damaged = json.loads(original)
        damaged['events'][0]['hash'] = 'f' * 64
        for raw in ('invalid JSON SYNTHETIC_PRIVATE_BODY', json.dumps(damaged)):
            store.path.write_text(raw)
            result = await self.run_check(request_id=str(uuid.uuid4()))
            self.assertEqual(result['status'], 'integrity_failure')
            self.assertNotIn('SYNTHETIC_PRIVATE_BODY', json.dumps(result))
        store.path.write_bytes(original)
        with patch.object(store, 'read', side_effect=ReceiptStorageError('receipt_storage_unavailable')):
            result = await self.run_check(request_id=str(uuid.uuid4()))
        self.assertEqual(result['status'], 'evidence_unavailable')
        self.assertEqual(self.gateway.calls, calls)
        self.assert_unchanged()

    async def test_tool_outer_timeout_is_reported_but_external_cancellation_is_not(self):
        from ha_mcp_engineering.tools import get_registered_server, registered_tools, governance
        from ha_mcp_engineering.governance import configuration_reverification as module
        from ha_mcp_engineering.request_context import begin_request, end_request
        entered = asyncio.Event()
        async def hung(*args):
            entered.set()
            await asyncio.Event().wait()
        tool = registered_tools(get_registered_server())['reverify_configuration_task']
        with patch.object(self.gateway, 'read', side_effect=hung), patch.object(module, 'DEADLINE_SECONDS', .02), patch.object(governance.GOVERNANCE, 'require', return_value=self.service):
            result = json.loads(await tool.run(self.binding))
        self.assertEqual(result['data']['status'], 'read_timeout')
        self.assertTrue(result['timing']['timeout_occurred'])
        self.assertEqual(result['timing']['retry_count'], 0)
        # Resolve the interrupted request, then externally cancel a fresh one.
        await self.run_check()
        entered.clear()
        telemetry, token = begin_request()
        try:
            with patch.object(self.gateway, 'read', side_effect=hung):
                pending = asyncio.create_task(self.run_check(request_id=str(uuid.uuid4())))
                await asyncio.wait_for(entered.wait(), 2)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
            self.assertFalse(telemetry.timeout_occurred)
            self.assertEqual(telemetry.ha_active_requests, 0)
        finally:
            end_request(token)
        self.assert_unchanged()

    async def test_authenticated_invalid_fields_do_not_echo_private_names_or_values(self):
        from tests.test_rc2dev6_auth_audit import asgi_request
        from tests.test_beta_observability import settings
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.routing import AuthenticatedMcpGateway
        from ha_mcp_engineering.tools import get_registered_server, registered_tools, governance
        tool = registered_tools(get_registered_server())['reverify_configuration_task']
        async def app(scope, receive, send):
            rpc = json.loads((await receive())['body'])
            rendered = await tool.run(rpc['params']['arguments'])
            payload = {'jsonrpc': '2.0', 'id': rpc['id'], 'result': {'content': [{'type': 'text', 'text': rendered}], 'isError': False}}
            await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json')]})
            await send({'type': 'http.response.body', 'body': json.dumps(payload).encode()})
        path = self.root / 'synthetic-auth-audit.jsonl'
        configured = settings(str(path))
        gateway = AuthenticatedMcpGateway(app, configured, AuditLogger(str(path), configured.access_secret))
        marker = 'SYNTHETIC_PRIVATE_UNTRUSTED_TEXT'
        for extra in ({marker: marker}, {marker * 100: marker}, {'task_id': marker}, {'request_id': marker}, {'expected_plan_hash': marker}, {'force': marker}):
            rpc = {'jsonrpc': '2.0', 'id': 'synthetic-audit-input', 'method': 'tools/call', 'params': {'name': tool.name, 'arguments': {**self.binding, **extra}}}
            with patch.object(governance.GOVERNANCE, 'require', return_value=self.service):
                status, response, _ = await asgi_request(gateway, f'/{configured.access_secret}/mcp', body=json.dumps(rpc).encode())
            self.assertEqual(status, 200)
            self.assertNotIn(marker, response.decode())
            self.assertNotIn(marker, path.read_text())
        self.assertEqual(self.gateway.calls, [])
        self.assert_unchanged()

    async def test_uncertain_start_commit_does_not_attempt_a_second_write(self):
        self.assertTrue((await self.run_check())['review_resolved'])
        calls = list(self.gateway.calls)
        stages = []
        def fail(stage):
            stages.append(stage)
            if stage == 'after_replace':
                raise OSError('SYNTHETIC_PRIVATE_DURABILITY')
        self.reverification.store.fault_hook = fail
        result = await self.run_check(request_id=str(uuid.uuid4()))
        self.assertEqual(result['status'], 'evidence_persistence_failed')
        self.assertTrue(result['receipt_commit_unknown'])
        self.assertTrue(result['evidence_uncertain'])
        self.assertEqual(stages, ['before_write', 'before_replace', 'after_replace'])
        self.assertEqual(self.gateway.calls, calls)
        self.assertFalse(self.service.get_execution_task(self.binding['task_id'])['review_resolution']['review_resolved'])
        self.assert_unchanged()

    async def test_one_free_receipt_slot_records_capacity_refusal(self):
        from ha_mcp_engineering.governance import configuration_reverification as module
        from ha_mcp_engineering.governance import reverification_storage as storage
        self.assertTrue((await self.run_check())['review_resolved'])
        calls = list(self.gateway.calls)
        with patch.object(module, 'MAX_EVENTS', 3), patch.object(storage, 'MAX_EVENTS', 3):
            result = await self.run_check(request_id=str(uuid.uuid4()))
            self.assertEqual(result['status'], 'receipt_capacity_exceeded')
            self.assertTrue(result['receipt_persisted'])
            self.assertEqual(len(self.reverification.store.read()), 3)
        self.runtime.configuration_reverification = ConfigurationReverification(self.runtime, str(self.root / 'plans'))
        self.assertFalse(self.service.get_execution_task(self.binding['task_id'])['review_resolution']['review_resolved'])
        self.assertTrue((await self.run_check())['replayed'])
        self.assertEqual(self.gateway.calls, calls)
        self.assert_unchanged()

    async def test_full_receipt_capacity_reports_uncertainty_without_inventing_durability(self):
        from ha_mcp_engineering.governance import configuration_reverification as module
        from ha_mcp_engineering.governance import reverification_storage as storage
        self.assertTrue((await self.run_check())['review_resolved'])
        before = self.reverification.store.path.read_bytes()
        calls = list(self.gateway.calls)
        with patch.object(module, 'MAX_EVENTS', 2), patch.object(storage, 'MAX_EVENTS', 2):
            # A mismatched input may not invalidate the known dated evidence.
            invalid = await self.run_check(request_id=str(uuid.uuid4()), expected_plan_hash='b' * 64)
            self.assertFalse(invalid['review_resolved'])
            self.assertTrue(self.service.get_execution_task(self.binding['task_id'])['review_resolution']['review_resolved'])
            result = await self.run_check(request_id=str(uuid.uuid4()))
            self.assertEqual(result['status'], 'receipt_capacity_exceeded')
            self.assertTrue(result['evidence_uncertain'])
            self.assertFalse(result['receipt_persisted'])
            self.assertFalse(self.service.get_execution_task(self.binding['task_id'])['review_resolution']['review_resolved'])
            self.assertEqual(self.reverification.store.path.read_bytes(), before)
        self.assertEqual(self.gateway.calls, calls)
        # Restart cannot manufacture the unrecorded refusal: surviving evidence
        # is still explicitly dated, never continuously verified configuration.
        self.runtime.configuration_reverification = ConfigurationReverification(self.runtime, str(self.root / 'plans'))
        surviving = self.service.get_execution_task(self.binding['task_id'])['review_resolution']
        self.assertEqual(surviving['request_id'], self.binding['request_id'])
        self.assertFalse(surviving['current_configuration_continuously_verified'])
        self.assert_unchanged()
