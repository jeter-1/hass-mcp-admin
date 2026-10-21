"""HAMCP-189 R1: fresh rollback planning holds its own Core lease.

``rollback_change`` is Core-independent at the gateway because its result can
be a local idempotent return.  Fresh rollback planning reads current
configuration, so the F3 runtime acquires and consumes a Core lease before the
first planning read and requires it to stay current through plan creation.
Under degraded Core the request is refused with zero reads and no plan.  All
stores and configuration peers are synthetic.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    GovernanceError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    F3RuntimeIntegration,
)
from ha_mcp_engineering.request_context import current_telemetry  # noqa: E402

from tests import (  # noqa: E402
    test_hamcp069a_entity_metadata_execution as entity_execution,
    test_hamcp135_configuration_eligibility as eligibility_fixtures,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)
from tests.test_f3_runtime_integration import (  # noqa: E402
    _ExactFakeConfigurationGateway,
    _provider_identity,
)


PLANNING = "core.dependency_helper_planning"


class LeaseCore:
    """A Core runtime that records the rollback lease lifecycle."""

    def __init__(self, *, admit=True, consume=True, valid_reads=None):
        self.admit = admit
        self.allow_consume = consume
        # None keeps the lease current; an integer N lets N revalidations
        # pass and refuses every later one (Core generation retired).
        self.valid_reads = valid_reads
        self.acquired: list[tuple] = []
        self.consumed = 0
        self.revalidated = 0
        self.finished = 0
        self.released = 0

    def acquire(self, requirements, **kwargs):
        self.acquired.append((tuple(requirements), kwargs))
        return object() if self.admit else None

    def consume(self, authority):
        self.consumed += 1
        return ("synthetic-commit",) if self.allow_consume else None

    def revalidate(self, authority, commits):
        self.revalidated += 1
        if self.valid_reads is None:
            return True
        return self.revalidated <= self.valid_reads

    def finish(self, commits):
        self.finished += 1
        return True

    def release(self, authority):
        self.released += 1
        return True

    @property
    def outstanding(self):
        return len([item for item in self.acquired if self.admit]) - (
            self.finished + self.released
        )


class RollbackCoreLeaseTests(ConfigurationPlanTestCase):
    create_retry_plan = eligibility_fixtures.EligibilityTests.create_retry_plan

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # The forward plan is applied without Core so these tests isolate the
        # rollback planning lease; apply-time Core is covered elsewhere.
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
        self.read_authorizations: list[bool] = []
        original_read = self.gateway.read

        async def observed_read(resource_type, resource_id):
            # Mirror the production REST client: a read refused by the
            # installed request authorizer raises before any request.
            telemetry = current_telemetry()
            installed = (
                telemetry is not None
                and telemetry.core_dispatch_authorizer is not None
            )
            authorized = installed and telemetry.authorize_core_dispatch()
            self.read_authorizations.append(authorized)
            if installed and not authorized:
                raise HomeAssistantUnavailableError()
            return await original_read(resource_type, resource_id)

        self.gateway.read = observed_read

    async def applied(self, created):
        await self.approve(created)
        result = await self.service.apply(created["plan_id"], created["plan_hash"])
        self.assertEqual(result["task_state"], "succeeded_verified")
        self.gateway.calls.clear()
        self.read_authorizations.clear()
        return created

    def reads(self):
        return [call for call in self.gateway.calls if call[0] == "read"]

    async def assert_refused_before_any_read(self, created, core):
        source_before = self.repository.get(created["plan_id"])
        request_before = source_before.rollback.request_id
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        details = raised.exception.details
        self.assertEqual(details["reason"], "core_capability_unavailable")
        self.assertIs(details["plan_created"], False)
        self.assertIs(details["provider_dispatch_occurred"], False)
        self.assertEqual(self.reads(), [])
        self.assertFalse(any(call[0] == "write" for call in self.gateway.calls))
        self.assertEqual(
            self.repository.get(created["plan_id"]).rollback.request_id,
            request_before,
        )
        self.assertEqual(core.outstanding, 0)
        return details

    async def test_degraded_core_refuses_fresh_rollback_before_any_read(self):
        created = await self.applied(await self.create_automation_plan())
        core = LeaseCore(admit=False)
        self.runtime.core_runtime = core
        await self.assert_refused_before_any_read(created, core)
        self.assertEqual(core.acquired, [((PLANNING,), {})])
        self.assertEqual(core.consumed, 0)

    async def test_unconsumable_lease_is_released_and_refused_before_any_read(self):
        created = await self.applied(await self.create_automation_plan())
        core = LeaseCore(consume=False)
        self.runtime.core_runtime = core
        await self.assert_refused_before_any_read(created, core)
        self.assertEqual(core.released, 1)
        self.assertEqual(core.finished, 0)

    async def test_admitted_core_creates_the_plan_inside_one_current_lease(self):
        created = await self.applied(await self.create_automation_plan())
        core = LeaseCore()
        self.runtime.core_runtime = core
        result = await self.service.rollback_change(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(result["status"], "rollback_plan_created")
        self.assertIs(result["approval_required"], True)
        self.assertIs(result["provider_dispatch_occurred"], False)
        self.assertEqual(core.acquired, [((PLANNING,), {})])
        self.assertEqual(core.consumed, 1)
        # Every planning read ran inside the authorized lease window.
        self.assertTrue(self.reads())
        self.assertTrue(self.read_authorizations)
        self.assertTrue(all(self.read_authorizations))
        self.assertEqual((core.finished, core.released), (1, 0))
        self.assertFalse(any(call[0] == "write" for call in self.gateway.calls))
        # The window is closed after planning: the request authorizer is
        # restored rather than left admitting later calls.
        self.assertIsNone(current_telemetry().core_dispatch_authorizer)

    async def test_existing_rollback_plan_is_returned_without_core(self):
        created = await self.applied(await self.create_automation_plan())
        self.runtime.core_runtime = LeaseCore()
        first = await self.service.rollback_change(
            created["plan_id"], created["plan_hash"]
        )
        self.gateway.calls.clear()
        degraded = LeaseCore(admit=False)
        self.runtime.core_runtime = degraded
        again = await self.service.rollback_change(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(again["rollback_plan_id"], first["rollback_plan_id"])
        self.assertEqual(again["plan_hash"], first["plan_hash"])
        self.assertEqual(degraded.acquired, [])
        self.assertEqual(self.reads(), [])

    async def test_core_retired_during_planning_creates_no_plan(self):
        created = await self.applied(await self.create_automation_plan())
        # The lease is current for the first planning read only.
        core = LeaseCore(valid_reads=1)
        self.runtime.core_runtime = core
        source_before = self.repository.get(created["plan_id"]).rollback.request_id
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(
            raised.exception.details["reason"], "core_capability_unavailable"
        )
        self.assertEqual(
            self.repository.get(created["plan_id"]).rollback.request_id,
            source_before,
        )
        self.assertEqual(core.outstanding, 0)

    async def test_garage_inverse_refuses_before_any_read_under_degraded_core(self):
        created = await self.applied(await self.create_retry_plan())
        core = LeaseCore(admit=False)
        self.runtime.core_runtime = core
        await self.assert_refused_before_any_read(created, core)
        self.assertEqual(core.acquired, [((PLANNING,), {})])

    async def test_garage_inverse_plans_inside_the_lease_and_returns_existing(self):
        created = await self.applied(await self.create_retry_plan())
        core = LeaseCore()
        self.runtime.core_runtime = core
        first = await self.service.rollback_change(
            created["plan_id"], created["plan_hash"]
        )
        self.assertIn(
            first["status"],
            {"rollback_plan_created", "rollback_partial_plan_created"},
        )
        self.assertTrue(self.read_authorizations)
        self.assertTrue(all(self.read_authorizations))
        self.assertEqual(core.outstanding, 0)
        self.gateway.calls.clear()
        degraded = LeaseCore(admit=False)
        self.runtime.core_runtime = degraded
        again = await self.service.rollback_change(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(again["rollback_plan_id"], first["rollback_plan_id"])
        self.assertEqual(degraded.acquired, [])
        self.assertEqual(self.reads(), [])

    def retire_during_final_builder_read(self, core, builder_read_count):
        """Retire Core while the plan builder's last awaited read runs.

        The configuration plan builder awaits one current-state read per
        operation and then saves synchronously.  Retiring after an earlier
        read would let the next read's authorizer refuse first, so Core is
        retired only when the last read completes; then only a check at the
        save boundary can keep the plan unsaved.
        """

        create = self.service._create_configuration_plan
        read = self.service._read_configuration_resource
        building = {"active": False}
        builder_reads: list[str] = []

        async def observed_create(*args, **kwargs):
            building["active"] = True
            try:
                return await create(*args, **kwargs)
            finally:
                building["active"] = False

        # Retire after the read returns, past the read client's own
        # post-response authority check, so nothing but the save boundary
        # remains to notice.
        async def retiring_read(resource_type, resource_id):
            value = await read(resource_type, resource_id)
            if building["active"]:
                builder_reads.append(resource_id)
                if len(builder_reads) == builder_read_count:
                    core.valid_reads = 0
            return value

        self.service._create_configuration_plan = observed_create
        self.service._read_configuration_resource = retiring_read

        def restore():
            self.service._create_configuration_plan = create
            self.service._read_configuration_resource = read

        return builder_reads, restore

    async def assert_final_read_retirement_saves_nothing(
        self, created, builder_read_count
    ):
        core = LeaseCore()
        self.runtime.core_runtime = core
        builder_reads, restore = self.retire_during_final_builder_read(
            core, builder_read_count
        )
        plans_before = {plan.plan_id for plan in self.repository.list()}
        rollback_before = copy.deepcopy(
            self.repository.get(created["plan_id"]).rollback
        )
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(
                created["plan_id"], created["plan_hash"]
            )
        restore()
        # Every builder read ran, so the refusal came after the last one.
        self.assertEqual(len(builder_reads), builder_read_count)
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        details = raised.exception.details
        self.assertEqual(details["reason"], "core_capability_unavailable")
        self.assertIs(details["plan_created"], False)
        self.assertIs(details["provider_dispatch_occurred"], False)
        self.assertEqual(
            {plan.plan_id for plan in self.repository.list()}, plans_before
        )
        self.assertEqual(
            self.repository.get(created["plan_id"]).rollback, rollback_before
        )
        self.assertFalse(any(call[0] == "write" for call in self.gateway.calls))
        self.assertEqual((core.finished, core.released), (1, 0))
        self.assertEqual(core.outstanding, 0)
        self.assertIsNone(current_telemetry().core_dispatch_authorizer)
        # The refusal is not sticky: current authority then plans normally.
        # The clean retry reads exactly as many resources, so the retirement
        # above really followed the builder's last read.
        retry = LeaseCore()
        self.runtime.core_runtime = retry
        retry_reads, restore = self.retire_during_final_builder_read(retry, None)
        try:
            result = await self.service.rollback_change(
                created["plan_id"], created["plan_hash"]
            )
        finally:
            restore()
        self.assertEqual(len(retry_reads), builder_read_count)
        self.assertIn(
            result["status"],
            {"rollback_plan_created", "rollback_partial_plan_created"},
        )
        self.assertEqual(retry.outstanding, 0)

    async def test_garage_retirement_during_final_builder_read_saves_nothing(self):
        created = await self.applied(await self.create_retry_plan())
        # The garage inverse builder reads its five planned resources.
        await self.assert_final_read_retirement_saves_nothing(created, 5)

    async def test_ordinary_retirement_during_final_builder_read_saves_nothing(self):
        # Review M1, ordinary path: the builder's private save-boundary hook
        # (O8 amendment, governance/service.py) re-checks the lease after the
        # final read and before the plan is saved.
        created = await self.applied(await self.create_automation_plan())
        # The current-state read, then the called-script capture read
        # (HAMCP-213: the fixture automation calls script.set_hvac_comfort).
        await self.assert_final_read_retirement_saves_nothing(created, 2)

    async def test_runtime_without_core_keeps_current_behavior(self):
        # Test and embedding compositions omit the Core runtime; production
        # always installs it.  The lease is then a no-op.
        created = await self.applied(await self.create_automation_plan())
        self.assertIsNone(self.runtime.core_runtime)
        result = await self.service.rollback_change(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(result["status"], "rollback_plan_created")
        self.assertTrue(self.reads())


class EntityMetadataRollbackCoreLeaseTests(entity_execution.ExecutionTestCase):
    """The entity metadata reverse plan uses the create_entity_metadata_plan
    requirement pair, bound to the exact entity target."""

    applied_forward = entity_execution.RollbackTests.applied_forward
    TARGET_BINDING = {
        "target": {
            "target_type": "entity_registry_metadata",
            "target_id": entity_execution.TARGET,
        }
    }

    def install_observed_websocket(self):
        self.ws_authorizations: list[bool] = []
        websocket = self.ws
        answer = websocket.command

        async def command(message):
            telemetry = current_telemetry()
            installed = (
                telemetry is not None
                and telemetry.core_dispatch_authorizer is not None
            )
            authorized = installed and telemetry.authorize_core_dispatch()
            self.ws_authorizations.append(authorized)
            if installed and not authorized:
                raise HomeAssistantUnavailableError()
            return await answer(message)

        websocket.command = command

    async def test_degraded_core_refuses_before_any_registry_read(self):
        created = await self.applied_forward()
        core = LeaseCore(admit=False)
        self.runtime.core_runtime = core
        commands = len(self.ws.commands)
        plans = len(self.repository.list())
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(
            raised.exception.details["reason"], "core_capability_unavailable"
        )
        self.assertIs(raised.exception.details["core_authority_acquired"], False)
        self.assertEqual(len(self.ws.commands), commands)
        self.assertEqual(len(self.repository.list()), plans)
        self.assertEqual(
            core.acquired,
            [(
                (PLANNING, "core.entity_registry_metadata_operation"),
                self.TARGET_BINDING,
            )],
        )
        self.assertEqual(core.outstanding, 0)

    async def test_admitted_core_plans_every_registry_read_inside_the_lease(self):
        created = await self.applied_forward()
        core = LeaseCore()
        self.runtime.core_runtime = core
        self.install_observed_websocket()
        rollback = await self.service.rollback_change(
            created["plan_id"], created["plan_hash"]
        )
        self.assertEqual(rollback["status"], "rollback_plan_created")
        self.assertEqual(rollback["restored_fields"], ["icon", "labels", "name"])
        self.assertTrue(self.ws_authorizations)
        self.assertTrue(all(self.ws_authorizations))
        self.assertEqual(len(self.ws.updates()), 1)
        self.assertEqual((core.finished, core.released), (1, 0))
        self.assertEqual(core.outstanding, 0)

    async def test_core_retired_during_registry_planning_creates_no_plan(self):
        created = await self.applied_forward()
        core = LeaseCore(valid_reads=1)
        self.runtime.core_runtime = core
        self.install_observed_websocket()
        plans = len(self.repository.list())
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(
                created["plan_id"], created["plan_hash"]
            )
        self.assertEqual(raised.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(
            raised.exception.details["reason"], "core_capability_unavailable"
        )
        self.assertEqual(len(self.repository.list()), plans)
        self.assertEqual(len(self.ws.updates()), 1)
        self.assertEqual(core.outstanding, 0)
