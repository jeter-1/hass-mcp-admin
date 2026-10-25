"""HAMCP-155: the delete's pre-dispatch drift read runs in its own Core window.

The real ``RestClient`` refuses a Home Assistant call made outside a Core
dispatch-authority window (``clients/rest.py``).  The governed delete re-reads
its target immediately before the DELETE; that read used to run before the
dispatch authority was committed, so real Core refused it and every delete
failed pre-dispatch, while the unit-test fakes (which skipped the check) let
it pass.  The fakes now enforce the same check
(``tests/core_authority_fakes.py``), and the read runs inside a short window
that acquires, consumes and releases its own Core lease.

Every test drives the shipped service, the F3 executor and runtime, and the
closed delete gateway against the enforcing synthetic Core store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.f3_configuration.delete import (  # noqa: E402
    ConfigurationDeleteAdapter,
)
from ha_mcp_engineering.f3_configuration.adapter import (  # noqa: E402
    PredispatchAuthorityRefused,
)
from ha_mcp_engineering.f3_runtime.runtime import (  # noqa: E402
    _core_call_window,
    _predispatch_read_window,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    current_telemetry,
    end_request,
)

from tests.hamcp155_fixtures import (  # noqa: E402
    ConfigurationDeleteServiceTestCase,
)
import tests.test_hamcp155_configuration_delete_admission as admission  # noqa: E402


def delete_path(resource_type: str, target_id: str) -> tuple[str, str]:
    return ("DELETE", f"/config/{resource_type}/config/{target_id}")


class PredispatchAuthorityWindowTests(ConfigurationDeleteServiceTestCase):
    def refusals(self) -> list:
        return list(getattr(self.config_rest, "authority_refusals", []))

    async def approved_with_gets(self) -> tuple[dict, int]:
        self.add("automation", "a")
        created = await self.approved("automation", "a")
        return created, self.config_rest.config_gets.get(("automation", "a"), 0)

    async def test_delete_verifies_with_one_dispatch_under_enforcing_client(self):
        created, gets = await self.approved_with_gets()
        authorized: list[bool] = []

        def observe(resource_type, target_id, ordinal):
            if ordinal == gets + 2:
                # The dispatch-time drift read: answered only because the
                # request's Core authorizer admits it.
                authorized.append(current_telemetry().authorize_core_dispatch())

        self.config_rest.after_config_get = observe
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertTrue(result["provider_dispatch_occurred"])
        self.assertEqual(self.config_rest.deletes(), [delete_path("automation", "a")])
        self.assertNotIn("a", self.config_rest.configs["automation"], "readback proves absence")
        self.assertEqual(authorized, [True])
        self.assertEqual(self.refusals(), [], "no call left a Core-authority window")

    async def test_drift_in_the_window_is_stale_target_state(self):
        created, gets = await self.approved_with_gets()

        def drift(resource_type, target_id, ordinal):
            if ordinal == gets + 1:
                self.config_rest.configs["automation"]["a"]["mode"] = "restart"

        self.config_rest.after_config_get = drift
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn("stale_target_state", self.child_codes(result["task_id"]))
        self.assertEqual(self.config_rest.config_gets[("automation", "a")], gets + 2)
        self.assertEqual(self.config_rest.deletes(), [])
        self.assertEqual(self.refusals(), [])

    async def test_authority_withdrawn_during_the_read_is_refused(self):
        created, gets = await self.approved_with_gets()

        def withdraw(resource_type, target_id, ordinal):
            if ordinal == gets + 2:
                self.core.withdraw()

        self.config_rest.after_config_get = withdraw
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn(
            "dispatch_authority_unavailable", self.child_codes(result["task_id"])
        )
        self.assertEqual(self.config_rest.deletes(), [])
        self.assertIn("a", self.config_rest.configs["automation"])

    async def test_authority_missing_when_the_window_opens_is_refused(self):
        created, gets = await self.approved_with_gets()
        original = type(self.core).acquire_f3
        calls: list[int] = []

        def acquire_f3(core, prepared, plan_created_at=None):
            calls.append(len(calls))
            if len(calls) == 2:
                # The dispatch guard holds the first lease; the window's own
                # acquisition is the second and Core no longer admits it.
                core.withdraw()
            return original(core, prepared, plan_created_at=plan_created_at)

        with mock.patch.object(type(self.core), "acquire_f3", acquire_f3):
            result = await self.apply(created)
        self.assertEqual(len(calls), 2, "the guard's lease, then the window's own")
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn(
            "dispatch_authority_unavailable", self.child_codes(result["task_id"])
        )
        self.assertEqual(
            self.config_rest.config_gets[("automation", "a")],
            gets + 1,
            "no dispatch-time read without a lease",
        )
        self.assertEqual(self.config_rest.deletes(), [])

    async def test_read_outside_any_window_fails_closed(self):
        """Mutation: the re-read moved back outside a Core-authority window."""

        created, gets = await self.approved_with_gets()
        with mock.patch.object(
            ConfigurationDeleteAdapter, "PREDISPATCH_READS_HOME_ASSISTANT", False
        ):
            result = await self.apply(created)
        self.assertNotEqual(result["task_state"], "succeeded_verified", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertEqual(self.config_rest.deletes(), [])
        self.assertIn(
            ("GET", "/config/automation/config/a"),
            self.refusals(),
            "the enforcing client refuses the authority-less read",
        )


class RecordingCore:
    """Lease double that records every call the window makes."""

    def __init__(self, *, authority="lease", commits=("commit",)):
        self.authority, self.commits = authority, commits
        self.current = True
        self.calls: list[tuple] = []

    def acquire_f3(self, prepared, plan_created_at=None):
        self.calls.append(("acquire_f3", prepared, plan_created_at))
        return self.authority

    def consume(self, authority):
        self.calls.append(("consume", authority))
        return self.commits

    def revalidate(self, authority, commits):
        self.calls.append(("revalidate", authority, commits))
        return self.current

    def release(self, authority):
        self.calls.append(("release", authority))
        return True

    def finish(self, commits):
        self.calls.append(("finish", commits))
        return True

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


class WindowUnitTests(unittest.IsolatedAsyncioTestCase):
    """``_predispatch_read_window`` in isolation, inside a dispatch window
    whose own authorizer (the uncommitted guard) refuses every call."""

    async def asyncSetUp(self):
        self.telemetry, token = begin_request()
        self.addCleanup(end_request, token)
        self.outer = [True]
        self.telemetry.core_dispatch_authorizer = lambda: self.outer[0]
        self.dispatch = _core_call_window(lambda: False)
        await self.dispatch.__aenter__()
        self.addAsyncCleanup(self.dispatch.__aexit__, None, None, None)
        self.dispatch_authorizer = self.telemetry.core_dispatch_authorizer
        self.guard = SimpleNamespace(commits=None, plan_created_at="created")
        self.core = RecordingCore()

    def window(self, guard="default"):
        return _predispatch_read_window(
            self.core, self.guard if guard == "default" else guard, "prepared", self.dispatch
        )

    async def test_admits_calls_only_inside_while_current(self):
        self.assertFalse(self.telemetry.authorize_core_dispatch())
        async with self.window():
            self.assertTrue(self.telemetry.authorize_core_dispatch())
            self.core.current = False
            self.assertFalse(self.telemetry.authorize_core_dispatch())
            self.core.current = True
        self.assertIs(self.telemetry.core_dispatch_authorizer, self.dispatch_authorizer)
        self.assertFalse(self.telemetry.authorize_core_dispatch())
        self.assertEqual(
            self.core.calls[:2],
            [("acquire_f3", "prepared", "created"), ("consume", "lease")],
        )
        self.assertEqual(self.core.calls[-1], ("finish", ("commit",)))

    async def test_outer_authorizer_still_binds(self):
        async with self.window():
            self.outer[0] = False
            self.assertFalse(self.telemetry.authorize_core_dispatch())
            self.outer[0] = True

    async def test_lease_lost_during_the_read_is_refused_and_finished(self):
        with self.assertRaises(PredispatchAuthorityRefused):
            async with self.window():
                self.core.current = False
        self.assertEqual(self.core.names()[-1], "finish")
        self.assertIs(self.telemetry.core_dispatch_authorizer, self.dispatch_authorizer)

    async def test_read_failure_propagates_and_finishes(self):
        with self.assertRaises(KeyError):
            async with self.window():
                raise KeyError("read")
        self.assertEqual(self.core.names()[-1], "finish")
        self.assertIs(self.telemetry.core_dispatch_authorizer, self.dispatch_authorizer)

    async def test_no_guard_or_committed_guard_refuses_before_core(self):
        for guard in (None, SimpleNamespace(commits=("c",), plan_created_at="x")):
            with self.subTest(guard=guard):
                with self.assertRaises(PredispatchAuthorityRefused):
                    async with self.window(guard):
                        self.fail("entered")
        self.assertEqual(self.core.calls, [])

    async def test_single_use(self):
        window = self.window()
        async with window:
            pass
        calls = len(self.core.calls)
        with self.assertRaises(PredispatchAuthorityRefused):
            async with window:
                self.fail("entered twice")
        self.assertEqual(len(self.core.calls), calls)

    async def test_missing_lease_refuses_without_consuming(self):
        self.core.authority = None
        with self.assertRaises(PredispatchAuthorityRefused):
            async with self.window():
                self.fail("entered")
        self.assertEqual(self.core.names(), ["acquire_f3"])
        self.assertIs(self.telemetry.core_dispatch_authorizer, self.dispatch_authorizer)

    async def test_unconsumable_lease_is_released(self):
        self.core.commits = None
        with self.assertRaises(PredispatchAuthorityRefused):
            async with self.window():
                self.fail("entered")
        self.assertEqual(self.core.names(), ["acquire_f3", "consume", "release"])
        self.assertIs(self.telemetry.core_dispatch_authorizer, self.dispatch_authorizer)


class SignedCoreWindowTests(ConfigurationDeleteServiceTestCase):
    """The real ``CoreRuntime`` (TEST-signed entry): a second, sequence-numbered
    lease is issued while the dispatch guard holds its own, and both finish."""

    async def signed_core(self):
        authority = admission.SignedTestRegistryAdmissionTests()
        await authority.asyncSetUp()
        self.addAsyncCleanup(authority.asyncTearDown)
        self.addCleanup(authority.doCleanups)
        await authority.publish(authority.with_reference())
        self.clock[0] = datetime.now(timezone.utc)
        return authority.core

    async def test_signed_core_delete_verifies_once(self):
        core = await self.signed_core()
        self.add("automation", "a")
        self.runtime.core_runtime = core
        created = await self.approved("automation", "a")
        result = await self.apply(created)
        self.assertEqual(result["task_state"], "succeeded_verified", result)
        self.assertEqual(self.config_rest.deletes(), [delete_path("automation", "a")])
        self.assertEqual(getattr(self.config_rest, "authority_refusals", []), [])
        self.assertEqual(core.health_snapshot()["fallback_count"], 0)


if __name__ == "__main__":
    unittest.main()
