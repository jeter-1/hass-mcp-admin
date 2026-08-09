"""Beta 20 activation contracts for the closed F3 runtime boundary."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from datetime import datetime, timedelta, timezone
from html import unescape
import re
import sys
import tempfile
import unittest
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3_runtime.registry import (  # noqa: E402
    CONFIGURATION_REGISTRATIONS,
    DASHBOARD_REGISTRATION,
    OPERATIONAL_REGISTRATIONS,
)
from ha_mcp_engineering.f3_runtime.repository import (  # noqa: E402
    CHILD_EXECUTION_NAMESPACE,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3_EXECUTION_AUTHORITY,
    F3RuntimeIntegration,
)
from ha_mcp_engineering.approval_web import (  # noqa: E402
    create_approval_application,
)
from ha_mcp_engineering.errors import ErrorCode  # noqa: E402
from ha_mcp_engineering.f3.contracts import (  # noqa: E402
    LockMode,
    LockRequest,
    LockScope,
)
from ha_mcp_engineering.f3.locks import (  # noqa: E402
    DurableLockStore,
    LockConflict,
    LockOwnershipError,
    LockStorageError,
)
from ha_mcp_engineering.f3.models import (  # noqa: E402
    LockOwner,
    LockTiming,
    LockToken,
)
from ha_mcp_engineering.clients.rest import ExpectedHttpStatus  # noqa: E402
from ha_mcp_engineering.governance.resources import (  # noqa: E402
    ConfigurationResourceGateway,
)
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import ChangePlanRepository  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.governance.task_models import (  # noqa: E402
    ExecutionTaskState,
    TASK_SCHEMA_VERSION,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
    PROPOSED_AUTOMATION,
    PROPOSED_SCRIPT,
)
from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    Clock,
    FakeLifecycleGateway,
    LegacyGateway,
)
from tests.test_2_1a_operational_backup import (  # noqa: E402
    FakeOperationalGateway,
)


async def _provider_identity():
    return {"slug": "core_ha_mcp", "evidence_hash": "a" * 64}


class _AbsentStateReader:
    async def request(self, *_args, **_kwargs):
        return ExpectedHttpStatus(404)


class _ExactFakeConfigurationGateway(ConfigurationResourceGateway):
    def __init__(self, delegate):
        self.delegate = delegate
        self.rest_client = _AbsentStateReader()
        self.websocket_client = object()

    async def get(self, resource_type, resource_id):
        return await self.delegate.read(resource_type, resource_id)

    async def read(self, resource_type, resource_id):
        return await self.delegate.read(resource_type, resource_id)

    async def validate_all(self):
        return await self.delegate.validate_all()

    async def write(self, action, resource_type, resource_id, proposed_config):
        return await self.delegate.write(
            action, resource_type, resource_id, proposed_config
        )


class _UnusedExactConfigurationGateway(ConfigurationResourceGateway):
    def __init__(self):
        self.rest_client = _AbsentStateReader()
        self.websocket_client = object()

    async def get(self, *_args):
        raise AssertionError("configuration gateway must not be used")

    async def read(self, *_args):
        raise AssertionError("configuration gateway must not be used")

    async def validate_all(self):
        raise AssertionError("configuration gateway must not be used")

    async def write(self, *_args):
        raise AssertionError("configuration gateway must not be used")


class _F3LifecycleGateway(FakeLifecycleGateway):
    """Extend the legacy lifecycle fake with the accepted C2 add-on identity."""

    async def planning_evidence(self, operation, target):
        result = await super().planning_evidence(operation, target)
        if operation != "restart_addon":
            return result
        baseline = result["baseline"]
        target_class = baseline["target_class"]
        addon = baseline["addon"]
        baseline["target_identity"] = {
            "requested_slug": target,
            "resolved_slug": addon["slug"],
            "resolved_name": addon["name"],
            "resolved_version": addon["version"],
            "resolved_repository": target.partition("_")[0],
            "identity_source": "synthetic_exact_inventory",
            "authoritative_self_match": target_class == "engineering_addon",
            "authoritative_upstream_match": False,
            "target_class": target_class,
        }
        baseline["upstream_addon_identity"] = {
            "status": "bound",
            "slug": "core_ha_mcp",
            "name": "Synthetic ha-mcp",
            "installed_version": "8.0.0",
            "repository": "core",
            "endpoint_host": "core-ha-mcp",
            "identity_source": (
                "configured_endpoint_supervisor_dns_and_reviewed_admission"
            ),
            "inventory_arguments": {
                "source": "installed",
                "include_stats": False,
            },
            "admission_evidence": result["provider"],
            "provider_contract": result["provider"],
        }
        return result


class SelectiveConflictHoldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
        self.timing = LockTiming(120, 20, 0, 0.05)
        self.owner = LockOwner(
            owner_id="owner-one",
            task_id="child-one",
            plan_id="plan-one",
            operation_id="restart_addon",
            attempt_id="attempt-one",
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _request(key, mode=LockMode.EXCLUSIVE, scope=LockScope.RESOURCE):
        return LockRequest(
            key=key,
            scopes=(scope,),
            mode=mode,
            reason_codes=("f3_runtime_acceptance",),
        )

    def test_selective_hold_releases_dependencies_but_retains_target_generation(self):
        store = DurableLockStore(self.temp.name)
        handle = store.acquire_once(
            (
                self._request("addon:local_test_addon"),
                self._request(
                    "addon:core_ha_mcp", LockMode.SHARED, LockScope.PROVIDER
                ),
            ),
            owner=self.owner,
            timing=self.timing,
            now=self.now,
        )
        target_token = next(
            item for item in handle.tokens if item.key == "addon:local_test_addon"
        )

        held = store.promote_selective_conflict_hold(
            handle,
            retained_keys=("addon:local_test_addon",),
            reason_code="manual_review_unresolved_dispatch",
        )

        self.assertEqual(held, (target_token,))
        records = store.records()
        self.assertEqual([(item.key, item.generation) for item in records], [
            (target_token.key, target_token.generation)
        ])
        self.assertTrue(records[0].conflict_hold)
        dependency = store.acquire_once(
            (self._request("addon:core_ha_mcp"),),
            owner=LockOwner(
                "owner-two", "child-two", "plan-two", "reload", "attempt-two"
            ),
            timing=self.timing,
            now=self.now + timedelta(days=1),
        )
        store.release(dependency)
        with self.assertRaises(LockConflict):
            store.acquire_once(
                (self._request("addon:local_test_addon"),),
                owner=LockOwner(
                    "owner-three", "child-three", "plan-three", "restart_addon",
                    "attempt-three",
                ),
                timing=self.timing,
                now=self.now + timedelta(days=1),
            )
        with self.assertRaises(LockOwnershipError):
            store.release_conflict_hold(
                owner=self.owner,
                tokens=(LockToken(target_token.key, target_token.generation + 1, "exclusive"),),
                reason_code="verified_resolution",
            )
        store.release_conflict_hold(
            owner=self.owner,
            tokens=held,
            reason_code="verified_resolution",
        )
        self.assertEqual(store.records(), ())

    def test_failed_selective_promotion_leaves_complete_handle_intact(self):
        def fault(stage):
            if stage == "during_selective_hold_promotion":
                raise OSError("synthetic process loss")

        store = DurableLockStore(self.temp.name, fault_hook=fault)
        handle = store.acquire_once(
            (
                self._request("reload:automation"),
                self._request("home_assistant:core", LockMode.SHARED),
            ),
            owner=self.owner,
            timing=self.timing,
            now=self.now,
        )
        with self.assertRaises(LockStorageError):
            store.promote_selective_conflict_hold(
                handle,
                retained_keys=("reload:automation",),
                reason_code="manual_review_unresolved_dispatch",
            )
        records = store.records()
        self.assertEqual(len(records), 2)
        self.assertFalse(any(item.conflict_hold for item in records))


class CrossAdapterLockGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DurableLockStore(self.temp.name)
        self.now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
        self.timing = LockTiming(120, 20, 0, 0.05)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _requests(values):
        return tuple(
            LockRequest(
                key=key,
                scopes=(
                    LockScope.PROVIDER
                    if key.startswith("addon:core_ha_mcp") and mode == LockMode.SHARED
                    else LockScope.RESOURCE,
                ),
                mode=mode,
                reason_codes=("cross_adapter_acceptance",),
            )
            for key, mode in values
        )

    @staticmethod
    def _owner(index):
        return LockOwner(
            f"owner-{index}", f"task-{index}", f"plan-{index}",
            f"operation-{index}", f"attempt-{index}",
        )

    def _assert_pair(self, first, second, *, conflict):
        first_handle = self.store.acquire_once(
            self._requests(first),
            owner=self._owner(1),
            timing=self.timing,
            now=self.now,
        )
        if conflict:
            with self.assertRaises(LockConflict):
                self.store.acquire_once(
                    self._requests(second),
                    owner=self._owner(2),
                    timing=self.timing,
                    now=self.now,
                )
        else:
            second_handle = self.store.acquire_once(
                self._requests(second),
                owner=self._owner(2),
                timing=self.timing,
                now=self.now,
            )
            self.store.release(second_handle)
        self.store.release(first_handle)

    def test_reviewed_cross_adapter_conflict_and_concurrency_matrix(self):
        configuration = (
            ("automation:fixture", LockMode.EXCLUSIVE),
            ("reload:automation", LockMode.SHARED),
            ("home_assistant:core", LockMode.SHARED),
        )
        cases = (
            ("same configuration target", configuration, configuration, True),
            (
                "configuration versus rollback",
                configuration,
                configuration,
                True,
            ),
            (
                "configuration versus matching reload",
                configuration,
                (
                    ("addon:core_ha_mcp", LockMode.SHARED),
                    ("home_assistant:core", LockMode.SHARED),
                    ("reload:automation", LockMode.EXCLUSIVE),
                ),
                True,
            ),
            (
                "configuration versus Home Assistant restart",
                configuration,
                (
                    ("addon:core_ha_mcp", LockMode.SHARED),
                    ("home_assistant:core", LockMode.EXCLUSIVE),
                ),
                True,
            ),
            (
                "configuration versus unrelated add-on restart",
                configuration,
                (
                    ("addon:core_ha_mcp", LockMode.SHARED),
                    ("addon:unrelated", LockMode.EXCLUSIVE),
                    ("home_assistant:core", LockMode.SHARED),
                ),
                False,
            ),
            (
                "independent configuration resources",
                configuration,
                (
                    ("script:fixture", LockMode.EXCLUSIVE),
                    ("reload:script", LockMode.SHARED),
                    ("home_assistant:core", LockMode.SHARED),
                ),
                False,
            ),
            (
                "backup versus Home Assistant restart",
                (
                    ("addon:core_ha_mcp", LockMode.SHARED),
                    ("backup:local_full_backup", LockMode.EXCLUSIVE),
                    ("home_assistant:core", LockMode.SHARED),
                ),
                (
                    ("addon:core_ha_mcp", LockMode.SHARED),
                    ("home_assistant:core", LockMode.EXCLUSIVE),
                ),
                True,
            ),
            (
                "ha-mcp restart versus provider-dependent operation",
                (
                    ("addon:core_ha_mcp", LockMode.EXCLUSIVE),
                    ("home_assistant:core", LockMode.SHARED),
                ),
                (
                    ("addon:core_ha_mcp", LockMode.SHARED),
                    ("backup:local_full_backup", LockMode.EXCLUSIVE),
                    ("home_assistant:core", LockMode.SHARED),
                ),
                True,
            ),
        )
        for name, first, second, conflict in cases:
            with self.subTest(name=name):
                self._assert_pair(first, second, conflict=conflict)


class _IngressRuntimeStub:
    def __init__(self):
        self.calls = []
        self.item = {
            "child_id": "child-f3-ingress",
            "public_task_id": "task-f3-ingress",
            "plan_id": "plan-f3-ingress",
            "operation_id": "update_automation_configuration",
            "ordinal": 0,
            "target": "automation:fixture",
            "prepared_hash": "a" * 64,
            "state": "manual_review",
            "normalized_outcome": "manual_review_required",
            "intent_timestamp": "2026-08-05T12:00:00+00:00",
            "evidence_deadline": "2026-08-05T12:10:00+00:00",
            "dispatch_count": 1,
            "provider_response_received": False,
            "observation_count": 2,
            "verification_count": 1,
            "selective_hold_keys": ["automation:fixture"],
            "hold_tokens": [
                {"key": "automation:fixture", "generation": 7, "mode": "exclusive"}
            ],
            "record_generation": 11,
            "reason_codes": ["evidence_deadline_expired"],
            "last_reconciliation_at": None,
            "reconciliation_result": None,
            "last_readback_summary": None,
        }

    def reconciliation_items(self):
        return [dict(self.item)]

    async def reconcile_child(self, **kwargs):
        if kwargs["action"] not in {
            "rerun_observation", "rerun_verification", "retain_hold",
            "release_hold_after_verified_resolution",
            "close_manual_review_unresolved", "create_governed_rollback_plan",
        }:
            raise GovernanceError(ErrorCode.INVALID_REQUEST)
        self.calls.append(kwargs)
        return {"status": "hold_retained"}


class _IngressGovernanceStub:
    def __init__(self, runtime):
        self.service = SimpleNamespace(f3_runtime=runtime)

    def require(self):
        return self.service


class F3IngressReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx

        self.runtime = _IngressRuntimeStub()
        app = create_approval_application(_IngressGovernanceStub(self.runtime))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app, client=("172.30.32.2", 12345)
            ),
            base_url="http://f3-ingress.local",
            headers={
                "X-Ingress-Path": "/api/hassio_ingress/f3fixture",
                "X-Remote-User-Id": "admin-fixture",
            },
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    @staticmethod
    def _hidden(body, name):
        match = re.search(
            rf'name="{re.escape(name)}" value="([^"]*)"', body
        )
        if match is None:
            raise AssertionError(f"missing hidden field {name}")
        return unescape(match.group(1))

    def _form(self, body, action="retain_hold"):
        return {
            "csrf": self._hidden(body, "csrf"),
            "record_generation": self._hidden(body, "record_generation"),
            "prepared_hash": self._hidden(body, "prepared_hash"),
            "hold_generation_binding": self._hidden(
                body, "hold_generation_binding"
            ),
            "action": action,
        }

    async def test_authenticated_ingress_action_is_csrf_and_generation_bound(self):
        review = await self.client.get("/f3/child-f3-ingress")
        self.assertEqual(review.status_code, 200)
        form = self._form(review.text)

        accepted = await self.client.post(
            "/f3/child-f3-ingress/reconcile", data=form
        )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertEqual(
            self.runtime.calls[0]["authorized_principal"],
            "home_assistant_admin_ingress:admin-fixture",
        )
        replay = await self.client.post(
            "/f3/child-f3-ingress/reconcile", data=form
        )
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(len(self.runtime.calls), 1)

    async def test_exact_private_action_vocabulary_is_forwarded(self):
        actions = (
            "rerun_observation",
            "rerun_verification",
            "retain_hold",
            "release_hold_after_verified_resolution",
            "close_manual_review_unresolved",
            "create_governed_rollback_plan",
        )
        for action in actions:
            with self.subTest(action=action):
                review = await self.client.get("/f3/child-f3-ingress")
                response = await self.client.post(
                    "/f3/child-f3-ingress/reconcile",
                    data=self._form(review.text, action),
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.runtime.calls[-1]["action"], action)
        self.assertEqual(len(self.runtime.calls), len(actions))

    async def test_stale_or_arbitrary_action_is_refused(self):
        review = await self.client.get("/f3/child-f3-ingress")
        stale = self._form(review.text)
        stale["record_generation"] = "12"
        response = await self.client.post(
            "/f3/child-f3-ingress/reconcile", data=stale
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.runtime.calls, [])

        review = await self.client.get("/f3/child-f3-ingress")
        arbitrary = self._form(review.text, "dispatch_provider_again")
        response = await self.client.post(
            "/f3/child-f3-ingress/reconcile", data=arbitrary
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.runtime.calls, [])

    async def test_non_ingress_or_unattributed_session_cannot_read_f3(self):
        response = await self.client.get(
            "/f3", headers={"X-Remote-User-Id": ""}
        )
        self.assertEqual(response.status_code, 403)
        import httpx

        app = create_approval_application(_IngressGovernanceStub(self.runtime))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
            base_url="http://f3-ingress.local",
            headers={
                "X-Ingress-Path": "/api/hassio_ingress/f3fixture",
                "X-Remote-User-Id": "admin-fixture",
            },
        ) as client:
            self.assertEqual((await client.get("/f3")).status_code, 403)


class F3ConfigurationActivationTests(ConfigurationPlanTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def test_ordered_configuration_plan_uses_one_public_task_and_children(self):
        created = await self.create_hvac_plan()
        await self.approve(created)

        applied = await self.service.apply(created["plan_id"], created["plan_hash"])
        task = self.service.task_repository.get(applied["task_id"])
        declarations = self.runtime.children.declarations_for_task(task.task_id)

        self.assertEqual(
            applied["task_state"],
            "succeeded_verified",
            [
                self.runtime.children.get(item["child_id"]).to_dict()
                if self.runtime.children.get(item["child_id"]) is not None
                else None
                for item in declarations
            ],
        )
        self.assertEqual(task.task_schema_version, TASK_SCHEMA_VERSION)
        self.assertEqual(
            task.legacy_projection["execution_authority"], F3_EXECUTION_AUTHORITY
        )
        self.assertEqual(len(declarations), 3)
        self.assertEqual(
            len(list((self.root / "plans" / CHILD_EXECUTION_NAMESPACE).glob("*.manifest.json"))),
            1,
        )
        self.assertEqual(
            [call for call in self.gateway.calls if call[0] == "write"],
            [
                ("write", "create", "input_number", "input_number.hvac_target"),
                ("write", "update", "script", "set_hvac_comfort"),
                ("write", "update", "automation", "apply_hvac_comfort"),
            ],
        )
        self.assertTrue(
            all(
                self.runtime.children.get(item["child_id"]).dispatch_count == 1
                for item in declarations
            )
        )

    async def test_duplicate_apply_reuses_authority_without_redispatch(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        first = await self.service.apply(created["plan_id"], created["plan_hash"])
        write_count = sum(call[0] == "write" for call in self.gateway.calls)

        second = await self.service.apply(created["plan_id"], created["plan_hash"])

        self.assertEqual(second["task_id"], first["task_id"])
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), write_count)

    async def test_two_runtime_instances_claim_one_public_and_child_authority(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        second_service = ChangeGovernanceService(
            ChangePlanRepository(self.root / "plans"),
            self.gateway,
            AuditLogger(str(self.audit_path), "dev14-test-access-secret"),
            now=self.service.now,
        )
        second_runtime = F3RuntimeIntegration(
            service=second_service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        second_service.f3_runtime = second_runtime
        await second_runtime.recover_once("startup")

        results = await asyncio.gather(
            self.service.apply(created["plan_id"], created["plan_hash"]),
            second_service.apply(created["plan_id"], created["plan_hash"]),
            return_exceptions=True,
        )

        self.assertFalse(
            [item for item in results if isinstance(item, BaseException)], results
        )
        self.assertEqual({item["task_id"] for item in results}, {
            self.service.task_repository.get_for_plan(created["plan_id"]).task_id
        })
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 1)
        self.assertEqual(len(self.service.task_repository.list()), 1)
        declarations = self.runtime.children.declarations_for_task(
            results[0]["task_id"]
        )
        self.assertEqual(len(declarations), 1)
        self.assertEqual(
            self.runtime.children.get(declarations[0]["child_id"]).dispatch_count,
            1,
        )

    async def test_process_loss_during_authority_initialization_recovers_one_sequence(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        failed = False

        def fail_first_task_write(stage):
            nonlocal failed
            if stage == "before_task_write" and not failed:
                failed = True
                raise OSError("synthetic ownership process loss")

        self.service.task_repository._fault_hook = fail_first_task_write
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.EXECUTION_TASK_STORAGE_ERROR)
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 0)
        self.assertTrue(
            (self.root / "plans" / CHILD_EXECUTION_NAMESPACE / ".initialization.json").exists()
        )

        self.service.task_repository._fault_hook = None
        reconstructed_service = ChangeGovernanceService(
            ChangePlanRepository(self.root / "plans"),
            self.gateway,
            AuditLogger(str(self.audit_path), "dev14-test-access-secret"),
            now=self.service.now,
        )
        reconstructed = F3RuntimeIntegration(
            service=reconstructed_service,
            storage_root=str(self.root / "plans"),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=None,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        reconstructed_service.f3_runtime = reconstructed
        task = reconstructed_service.task_repository.get_for_plan(created["plan_id"])
        declarations = reconstructed.children.declarations_for_task(task.task_id)
        self.assertEqual(len(reconstructed_service.task_repository.list()), 1)
        self.assertEqual(len(declarations), 1)
        self.assertIsNone(reconstructed.children.get(declarations[0]["child_id"]))
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 0)

        await reconstructed.recover_once("startup")
        terminal = reconstructed_service.task_repository.get(task.task_id)
        self.assertEqual(terminal.state.value, "succeeded_verified")
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 1)
        self.assertEqual(
            reconstructed.children.get(declarations[0]["child_id"]).dispatch_count,
            1,
        )

    async def test_configuration_response_loss_recovers_by_readback_without_redispatch(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        self.gateway.fail_after_write_target = (
            "automation",
            "apply_hvac_comfort",
        )

        result = await self.service.apply(created["plan_id"], created["plan_hash"])
        declaration = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]
        record = self.runtime.children.get(declaration["child_id"])

        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(record.dispatch_count, 1)
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 1)
        self.assertIn(
            '"event": "f3_provider_response_lost"',
            self.audit_path.read_text(encoding="utf-8"),
        )

    async def test_active_legacy_configuration_task_blocks_conflicting_f3_plan(self):
        historical = await self.create_automation_plan()
        await self.approve(historical)
        historical_plan = self.service._load(historical["plan_id"])
        legacy_task = self.service._create_task_for_plan(
            historical_plan, historical["plan_hash"]
        )
        self.service._record_task_event(
            legacy_task,
            "preflight_started",
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()},
        )
        current = await self.create_automation_plan()
        await self.approve(current)

        result = await self.service.apply(current["plan_id"], current["plan_hash"])
        declaration = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]
        child = self.runtime.children.get(declaration["child_id"])

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertEqual(child.normalized_outcome, "preflight_rejected")
        self.assertTrue(
            any(
                "legacy_active_task_conflict" in event["diagnostic_codes"]
                for event in child.events
            )
        )
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 0)

    async def test_durable_audit_binds_intent_provider_and_verification_events(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        await self.service.apply(created["plan_id"], created["plan_hash"])

        audit = self.audit_path.read_text(encoding="utf-8")
        self.assertIn('"event": "f3_dispatch_intent_committed"', audit)
        self.assertIn('"event": "f3_provider_invocation_started"', audit)
        self.assertIn('"event": "f3_provider_response_received"', audit)
        self.assertIn('"event": "f3_verification_recorded"', audit)
        self.assertNotIn("F3 fixture automation", audit)

    async def test_pre_intent_cancellation_materializes_a_durable_child_fence(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        plan = self.service._load(created["plan_id"])
        task, _prepared, _requests = await self.runtime._initialize(
            plan, created["plan_hash"]
        )

        result = await self.service.cancel_execution_task(task.task_id)
        declaration = self.runtime.children.declarations_for_task(task.task_id)[0]
        child = self.runtime.children.get(declaration["child_id"])

        self.assertEqual(result["status"], "cancelled_pre_dispatch")
        self.assertEqual(child.normalized_outcome, "cancelled_pre_dispatch")
        self.assertIsNone(child.dispatch_intent)
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 0)
        await self.runtime.recover_once("test")
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 0)

    async def test_all_eight_configuration_capabilities_share_one_sequence(self):
        self.gateway.configs[("input_boolean", "input_boolean.existing_flag")] = {
            "id": "existing_flag", "name": "Existing flag"
        }
        self.gateway.configs[("input_number", "input_number.existing_level")] = {
            "id": "existing_level", "name": "Existing level",
            "min": 0, "max": 10, "step": 1,
        }
        automation = {
            "alias": "F3 fixture automation", "mode": "single",
            "trigger": [{"platform": "state", "entity_id": "input_boolean.source"}],
            "condition": [],
            "action": [{"service": "light.turn_on", "target": {"entity_id": "light.fixture"}}],
        }
        script = {
            "alias": "F3 fixture script", "mode": "single",
            "sequence": [{"service": "light.turn_on", "target": {"entity_id": "light.fixture"}}],
        }
        definitions = (
            ("create_auto", "automation", None, "create", "f3_created", automation),
            ("update_auto", "automation", None, "update", "apply_hvac_comfort", automation),
            ("create_script", "script", None, "create", "f3_created", script),
            ("update_script", "script", None, "update", "set_hvac_comfort", script),
            ("create_boolean", "helper", "input_boolean", "create", "input_boolean.f3_created", {"name": "F3 created"}),
            ("update_boolean", "helper", "input_boolean", "update", "input_boolean.existing_flag", {"name": "F3 updated"}),
            ("create_number", "helper", "input_number", "create", "input_number.f3_created", {"name": "F3 created", "min": 0, "max": 100, "step": 5, "mode": "slider"}),
            ("update_number", "helper", "input_number", "update", "input_number.existing_level", {"name": "F3 updated", "min": 0, "max": 20, "step": 2, "mode": "slider"}),
        )
        operations = []
        for index, (operation_id, resource, helper, action, target, proposed) in enumerate(definitions):
            item = {
                "operation_id": operation_id,
                "resource_type": resource,
                "action": action,
                "target_id": target,
                "depends_on": [] if index == 0 else [definitions[index - 1][0]],
                "proposed_config": proposed,
            }
            if helper is not None:
                item["helper_type"] = helper
            operations.append(item)
        try:
            created = await self.service.create_configuration_plan(
                title="All F3 configuration capability routes",
                description="Synthetic eight-operation Beta 20 acceptance sequence",
                operations=operations,
            )
        except GovernanceError as exc:
            self.fail(exc.details)
        await self.approve(created)

        result = await self.service.apply(created["plan_id"], created["plan_hash"])
        declarations = self.runtime.children.declarations_for_task(result["task_id"])

        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(len(declarations), 8)
        self.assertEqual(
            {item["capability_id"] for item in declarations},
            {item[0] for item in CONFIGURATION_REGISTRATIONS},
        )
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), 8)

    async def test_rollback_creates_a_new_governed_plan_without_writing(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        await self.service.apply(created["plan_id"], created["plan_hash"])
        write_count = sum(call[0] == "write" for call in self.gateway.calls)

        result = await self.service.rollback_change(
            created["plan_id"], created["plan_hash"]
        )

        self.assertEqual(result["status"], "rollback_plan_created")
        self.assertTrue(result["approval_required"])
        self.assertNotEqual(result["rollback_plan_id"], created["plan_id"])
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), write_count)

    async def test_partial_sequence_rollback_includes_only_verified_updates(self):
        created = await self.service.create_configuration_plan(
            title="Partial sequence rollback fixture",
            description="Reverse only the first exact verified update",
            operations=[
                {
                    "operation_id": "verified_automation_update",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": "apply_hvac_comfort",
                    "depends_on": [],
                    "proposed_config": copy.deepcopy(PROPOSED_AUTOMATION),
                },
                {
                    "operation_id": "failed_script_update",
                    "resource_type": "script",
                    "action": "update",
                    "target_id": "set_hvac_comfort",
                    "depends_on": ["verified_automation_update"],
                    "proposed_config": copy.deepcopy(PROPOSED_SCRIPT),
                },
            ],
        )
        await self.approve(created)
        self.gateway.fail_write_target = ("script", "set_hvac_comfort")

        applied = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.assertIn(
            applied["task_state"],
            {"failed_post_dispatch", "manual_review_required"},
        )
        write_count = sum(call[0] == "write" for call in self.gateway.calls)
        source = self.service._load(created["plan_id"])

        rollback = await self.service.rollback_change(
            source.plan_id, self.service.plan_hash(source)
        )
        reverse = self.service._load(rollback["rollback_plan_id"])

        self.assertEqual(rollback["status"], "rollback_plan_created")
        self.assertEqual(
            rollback["operations_excluded"], ["failed_script_update"]
        )
        self.assertEqual(len(reverse.operations), 1)
        self.assertEqual(reverse.operations[0].target_id, "apply_hvac_comfort")
        self.assertEqual(reverse.operations[0].action, "update")
        self.assertEqual(
            sum(call[0] == "write" for call in self.gateway.calls), write_count
        )

    async def test_historical_legacy_configuration_task_migrates_rollback_to_f3(self):
        created = await self.create_automation_plan()
        await self.approve(created)
        active_runtime = self.service.f3_runtime
        self.service.f3_runtime = None
        legacy_result = await self.service.apply(
            created["plan_id"], created["plan_hash"]
        )
        self.service.f3_runtime = active_runtime
        legacy_task = self.service.task_repository.get(legacy_result["task_id"])
        self.assertNotEqual(
            legacy_task.legacy_projection.get("execution_authority"),
            F3_EXECUTION_AUTHORITY,
        )
        source = self.service._load(created["plan_id"])
        write_count = sum(call[0] == "write" for call in self.gateway.calls)

        rollback = await self.service.rollback_change(
            source.plan_id, self.service.plan_hash(source)
        )

        self.assertEqual(rollback["status"], "rollback_plan_created")
        self.assertEqual(sum(call[0] == "write" for call in self.gateway.calls), write_count)
        rollback_request = {
            "plan_id": rollback["rollback_plan_id"],
            "plan_hash": rollback["plan_hash"],
        }
        await self.approve(rollback_request)
        applied = await self.service.apply(
            rollback_request["plan_id"], rollback_request["plan_hash"]
        )
        rollback_task = self.service.task_repository.get(applied["task_id"])
        self.assertEqual(applied["task_state"], "succeeded_verified")
        self.assertEqual(
            rollback_task.legacy_projection["execution_authority"],
            F3_EXECUTION_AUTHORITY,
        )

    def test_registry_is_closed_and_contains_one_dashboard_update(self):
        identities = {entry.capability_id for entry in self.runtime.registry.entries}
        self.assertEqual(
            identities,
            {
                item[0]
                for item in (
                    *CONFIGURATION_REGISTRATIONS,
                    *OPERATIONAL_REGISTRATIONS,
                    DASHBOARD_REGISTRATION,
                )
            },
        )
        self.assertEqual(len(identities), 13)
        self.assertEqual(
            [item for item in identities if "dashboard" in item],
            ["update_existing_dashboard"],
        )

    def test_ready_health_is_bounded_and_reports_one_dashboard_capability(self):
        health = self.runtime.health()
        self.assertEqual(health["status"], "ready")
        self.assertTrue(health["execution_ready"])
        self.assertEqual(health["activated_capability_count"], 13)
        self.assertEqual(health["dashboard_capability_count"], 1)
        self.assertEqual(health["fallback_count"], 0)
        self.assertEqual(health["recovery_cadence_seconds"], 30)
        self.assertLessEqual(health["recovery_batch_size"], 16)


class _OperationalActivationBase(unittest.IsolatedAsyncioTestCase):
    async def _grant(self, created):
        plan = created["plan"]
        pending = self.service.approve(plan["plan_id"], plan["plan_hash"])
        _, csrf = await self.service.issue_external_csrf(
            plan["plan_id"], pending["challenge_id"]
        )
        result = await self.service.decide_external_approval(
            plan_id=plan["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=plan["plan_hash"],
            approval_kind="apply",
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal="home_assistant_admin_ingress:f3-fixture",
        )
        if result.get("status") == "approval_pending":
            _, csrf = await self.service.issue_external_csrf(
                plan["plan_id"], result["challenge_id"]
            )
            await self.service.decide_external_approval(
                plan_id=plan["plan_id"],
                challenge_id=result["challenge_id"],
                expected_plan_hash=plan["plan_hash"],
                approval_kind="apply",
                approval_action=result["approval_action"],
                csrf_nonce=csrf,
                decision="approve",
                approver_principal="home_assistant_admin_ingress:f3-fixture",
            )
        return plan

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()


class F3OperationalActivationTests(_OperationalActivationBase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.lifecycle = _F3LifecycleGateway()
        self.lifecycle.now = self.clock
        self.service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "plans"),
            LegacyGateway(),
            AuditLogger(str(Path(self.temp.name) / "audit.jsonl"), "f3-test-secret"),
            now=self.clock,
            lifecycle_gateway=self.lifecycle,
        )
        self.telemetry, self.context = begin_request("f3-operational-plan")
        self.telemetry.caller_id = "mcp-requester"
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(Path(self.temp.name) / "plans"),
            configuration_gateway=_UnusedExactConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=self.lifecycle,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def test_controlled_reload_routes_once_through_f3(self):
        created = await self.service.create_reload_plan(reload_target="automation")
        plan = await self._grant(created)

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        declaration = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]
        record = self.runtime.children.get(declaration["child_id"])

        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        self.assertEqual(record.dispatch_count, 1)
        self.assertEqual(record.normalized_outcome, "succeeded_verified")

    async def test_operational_response_loss_uses_observation_without_redispatch(self):
        self.lifecycle.mode = "ambiguous"
        created = await self.service.create_reload_plan(reload_target="automation")
        plan = await self._grant(created)

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        declaration = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]
        record = self.runtime.children.get(declaration["child_id"])

        self.assertEqual(result["task_state"], "observing")
        self.assertEqual(record.dispatch_count, 1)
        self.assertEqual(self.lifecycle.dispatch_count, 1)
        self.assertGreaterEqual(record.observation_attempts, 1)
        self.clock.advance(seconds=901)
        await self.runtime.recover_once("duplicate_response_loss_sweep")
        recovered = self.service.get_execution_task(result["task_id"])
        self.assertEqual(recovered["state"], "manual_review_required")
        self.assertEqual(
            [
                item.key
                for item in self.runtime.locks.records()
                if item.conflict_hold
            ],
            ["reload:automation"],
        )
        await self.runtime.recover_once("manual_review_duplicate_sweep")
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_active_legacy_reload_task_blocks_conflicting_f3_reload(self):
        historical = await self.service.create_reload_plan(
            reload_target="automation"
        )
        historical_plan = await self._grant(historical)
        persisted = self.service._load(historical_plan["plan_id"])
        legacy_task = self.service._create_task_for_plan(
            persisted, historical_plan["plan_hash"]
        )
        self.service._record_task_event(
            legacy_task,
            "preflight_started",
            new_state=ExecutionTaskState.PREFLIGHT,
            changes={"started_at": self.service._timestamp()},
        )
        current = await self.service.create_reload_plan(
            reload_target="automation"
        )
        current_plan = await self._grant(current)

        result = await self.service.apply(
            current_plan["plan_id"], current_plan["plan_hash"]
        )
        declaration = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]
        child = self.runtime.children.get(declaration["child_id"])

        self.assertEqual(result["task_state"], "failed_pre_dispatch")
        self.assertEqual(child.normalized_outcome, "preflight_rejected")
        self.assertTrue(
            any(
                "legacy_active_task_conflict" in event["diagnostic_codes"]
                for event in child.events
            )
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_post_intent_cancellation_is_refused_without_redispatch(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self._grant(created)
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(result["task_state"], "observing")

        with self.assertRaises(GovernanceError) as raised:
            await self.service.cancel_execution_task(result["task_id"])

        self.assertEqual(
            raised.exception.code,
            ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH,
        )
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_manual_review_retains_only_target_and_verified_release_is_fenced(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self._grant(created)
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.clock.advance(seconds=1900)

        await self.runtime.recover_once("test")

        task = self.service.get_execution_task(result["task_id"])
        declaration = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]
        child_runtime = self.runtime.children.runtime(declaration["child_id"])
        held = [item for item in self.runtime.locks.records() if item.conflict_hold]
        self.assertEqual(task["state"], "manual_review_required")
        self.assertEqual([item.key for item in held], ["home_assistant:core"])
        self.assertFalse(
            any(item.key == "addon:core_ha_mcp" for item in self.runtime.locks.records())
        )
        hold_binding = ",".join(
            f"{item['key']}:{item['generation']}"
            for item in child_runtime["selective_hold_tokens"]
        )

        released = await self.runtime.reconcile_child(
            child_id=declaration["child_id"],
            action="release_hold_after_verified_resolution",
            record_generation=child_runtime["record_generation"],
            prepared_hash=declaration["prepared_operation_hash"],
            hold_generation_binding=hold_binding,
            authorized_principal="home_assistant_admin_ingress:f3-fixture",
        )

        self.assertEqual(
            released["status"], "hold_released_after_verified_resolution"
        )
        self.assertEqual(self.runtime.locks.records(), ())
        self.assertEqual(self.lifecycle.dispatch_count, 1)

    async def test_process_loss_during_authorized_hold_release_finishes_without_dispatch(self):
        created = await self.service.create_home_assistant_restart_plan()
        plan = await self._grant(created)
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.clock.advance(seconds=1900)
        await self.runtime.recover_once("test")
        declaration = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]
        child_runtime = self.runtime.children.runtime(declaration["child_id"])
        hold_binding = ",".join(
            f"{item['key']}:{item['generation']}"
            for item in child_runtime["selective_hold_tokens"]
        )
        original_release = self.runtime.locks.release_conflict_hold

        def process_loss(**_kwargs):
            raise RuntimeError("synthetic process loss")

        self.runtime.locks.release_conflict_hold = process_loss
        with self.assertRaises(RuntimeError):
            await self.runtime.reconcile_child(
                child_id=declaration["child_id"],
                action="release_hold_after_verified_resolution",
                record_generation=child_runtime["record_generation"],
                prepared_hash=declaration["prepared_operation_hash"],
                hold_generation_binding=hold_binding,
                authorized_principal="home_assistant_admin_ingress:f3-fixture",
            )
        self.runtime.locks.release_conflict_hold = original_release
        journal = self.runtime.children.runtime(declaration["child_id"])
        self.assertIsNotNone(journal["hold_release_authority"])
        dispatches = self.lifecycle.dispatch_count

        reconstructed = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(Path(self.temp.name) / "plans"),
            configuration_gateway=_UnusedExactConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=self.lifecycle,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = reconstructed

        self.assertEqual(reconstructed.locks.records(), ())
        finalized = reconstructed.children.runtime(declaration["child_id"])
        self.assertIsNone(finalized["hold_release_authority"])
        self.assertEqual(finalized["selective_hold_tokens"], [])
        self.assertEqual(self.lifecycle.dispatch_count, dispatches)

    async def test_every_reload_domain_and_restart_route_uses_one_child(self):
        cases = [
            ("reload", target) for target in (
                "automation", "script", "input_boolean", "input_number"
            )
        ] + [
            ("addon", "local_test_addon"),
            ("home_assistant", "core"),
        ]
        for kind, target in cases:
            with self.subTest(kind=kind, target=target):
                if kind == "reload":
                    created = await self.service.create_reload_plan(
                        reload_target=target
                    )
                elif kind == "addon":
                    created = await self.service.create_addon_restart_plan(
                        addon_slug=target
                    )
                else:
                    created = await self.service.create_home_assistant_restart_plan()
                plan = await self._grant(created)
                before = self.lifecycle.dispatch_count

                result = await self.service.apply(
                    plan["plan_id"], plan["plan_hash"]
                )
                if kind == "home_assistant":
                    # The accepted restart adapter deliberately requires a
                    # later read-only observation after the initial disruption.
                    for _ in range(2):
                        if result.get("task_state", result.get("state")) == (
                            "succeeded_verified"
                        ):
                            break
                        self.clock.advance(seconds=120)
                        await self.runtime.recover_once("test")
                        result = self.service.get_execution_task(result["task_id"])
                declaration = self.runtime.children.declarations_for_task(
                    result["task_id"]
                )[0]
                record = self.runtime.children.get(declaration["child_id"])

                self.assertEqual(
                    result.get("task_state", result.get("state")),
                    "succeeded_verified",
                    record.to_dict(),
                )
                self.assertEqual(self.lifecycle.dispatch_count, before + 1)
                self.assertEqual(record.dispatch_count, 1)


class F3BackupActivationTests(_OperationalActivationBase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.gateway = FakeOperationalGateway()
        self.service = ChangeGovernanceService(
            ChangePlanRepository(Path(self.temp.name) / "plans"),
            LegacyGateway(),
            AuditLogger(str(Path(self.temp.name) / "audit.jsonl"), "f3-test-secret"),
            now=self.clock,
            operational_gateway=self.gateway,
        )
        self.telemetry, self.context = begin_request("f3-backup-plan")
        self.telemetry.caller_id = "mcp-requester"
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(Path(self.temp.name) / "plans"),
            configuration_gateway=_UnusedExactConfigurationGateway(),
            backup_gateway=self.gateway,
            lifecycle_gateway=None,
            provider_identity_reader=_provider_identity,
            retention_days=90,
        )
        self.service.f3_runtime = self.runtime
        await self.runtime.recover_once("startup")

    async def test_full_backup_routes_once_through_f3(self):
        created = await self.service.create_backup_plan(backup_name="F3 disposable")
        plan = await self._grant(created)

        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        declaration = self.runtime.children.declarations_for_task(
            result["task_id"]
        )[0]
        record = self.runtime.children.get(declaration["child_id"])

        self.assertEqual(result["task_state"], "succeeded_verified")
        self.assertEqual(self.gateway.dispatch_count, 1)
        self.assertEqual(record.dispatch_count, 1)


if __name__ == "__main__":
    unittest.main()
