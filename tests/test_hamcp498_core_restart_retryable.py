"""HAMCP-498: a Core restart window is a retryable CoreCapabilityUnavailable.

When admitted Core authority is withdrawn by a connection movement (a Core
restart or update), the runtime re-verifies it on its recovery pacing.  The
static gateway refused every Core-bound call in that window with the generic,
non-retryable ``CoreCapabilityUnavailable``, which reads the same as a Core
version Engineering does not support.  The refusal now names the open window
and is retryable; every other cause keeps the generic refusal.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    AVAILABILITY_EXPOSURE_TOOL,
)

# Imported as modules so that their test classes are not collected here.
import tests.test_ha_core_2026_9_integration as core9  # noqa: E402
import tests.test_hamcp069a_entity_metadata_readmission as readmission  # noqa: E402
from tests.test_ha_core_capability_auto_readmission import (  # noqa: E402
    _evidence,
    _snapshot,
)


RETIRED = "2026-11-25T15:20:14+00:00"
WINDOW_KEYS = {
    "reason", "reason_code", "recovery_state", "retired_at",
    "next_recovery_attempt_at", "confirmations", "confirmations_required",
    "provider_dispatch_occurred",
}


def refusal(body: bytes) -> dict:
    """The tool result: a gateway refusal (SSE) or the recording app's JSON."""
    data = [
        line[len(b"data: "):] for line in body.splitlines() if line.startswith(b"data: ")
    ]
    (content,) = json.loads(data[0] if data else body)["result"]["content"]
    return json.loads(content["text"])


async def call(gateway, tool_name: str, arguments: dict | None = None) -> dict:
    return refusal(await readmission.call(gateway, tool_name, arguments))


class RealRuntimeRestartWindowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            core9._core_2026_9_snapshot()
        )
        self.gateway, self.app = readmission.gateway_for(self.runtime)

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    def assert_window(self, payload: dict, state: str) -> None:
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "CoreCapabilityUnavailable")
        self.assertTrue(payload["retryable"])
        details = payload["details"]
        self.assertEqual(set(details), WINDOW_KEYS)
        self.assertEqual(details["reason_code"], "core_authority_recovering")
        self.assertEqual(details["reason"], "core_authority_recovering")
        self.assertEqual(details["recovery_state"], state)
        self.assertIsNotNone(details["retired_at"])
        self.assertEqual(details["confirmations_required"], 2)
        self.assertIs(details["provider_dispatch_occurred"], False)

    async def test_a_connection_movement_opens_a_retryable_window(self):
        admitted = await call(self.gateway, "list_areas")
        self.assertTrue(admitted["success"])
        self.assertEqual(len(self.app.calls), 1)

        self.runtime.request_reconciliation(connection_changed=True)
        recovery = self.runtime.health_projection()["recovery"]
        self.assertEqual(recovery["recovery_state"], "retrying")

        for tool_name, arguments in (
            ("list_areas", {}),
            ("server_info", {}),
            ("get_server_health", {"check_ha": True}),
            ("get_error_log", {}),
        ):
            with self.subTest(tool=tool_name):
                payload = await call(self.gateway, tool_name, arguments)
                self.assert_window(payload, "retrying")
                self.assertEqual(payload["details"]["retired_at"], recovery["retired_at"])
        # Nothing reached the application while the window was open.
        self.assertEqual(len(self.app.calls), 1)
        self.assertEqual(self.runtime.health_snapshot()["fallback_count"], 0)

        # A call with no Core requirement is not gated by the window.
        health = await call(self.gateway, "get_server_health", {"check_ha": False})
        self.assertTrue(health["success"])
        self.assertEqual(len(self.app.calls), 2)

    async def test_the_window_stays_retryable_while_confirming_then_closes(self):
        self.runtime.request_reconciliation(connection_changed=True)
        await self.runtime.reconcile_once("failure_retry")
        self.assertEqual(
            self.runtime.health_projection()["recovery"]["recovery_state"], "confirming"
        )
        payload = await call(self.gateway, "list_areas")
        self.assert_window(payload, "confirming")
        self.assertEqual(payload["details"]["confirmations"], 1)

        await self.runtime.reconcile_once("failure_retry")
        recovery = self.runtime.health_projection()["recovery"]
        self.assertEqual(recovery["recovery_state"], "restored")
        admitted = await call(self.gateway, "list_areas")
        self.assertTrue(admitted["success"])
        self.assertEqual(len(self.app.calls), 1)

    async def test_a_pending_signed_admission_keeps_precedence(self):
        self.runtime.request_reconciliation(connection_changed=True)
        payload = await call(
            self.gateway, AVAILABILITY_EXPOSURE_TOOL,
            {"operation": "disable", "entity_ids": ["light.porch"]},
        )
        self.assertEqual(payload["error"], "CoreCapabilityUnavailable")
        self.assertEqual(payload["details"]["reason_code"], "pending_signed_registry_admission")
        self.assertFalse(payload["retryable"])
        self.assertEqual(self.app.calls, [])


class RealRuntimeUnsupportedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def test_a_missing_capability_on_a_stable_runtime_stays_non_retryable(self):
        partial = [
            item for item in core9._core_2026_9_evidence()
            if item["capability_id"] != "core.basic_websocket_read"
        ]
        runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            core9._snapshot("2026.9.0", evidence=partial)
        )
        self.assertEqual(runtime.health_projection()["recovery"]["recovery_state"], "stable")
        gateway, app = readmission.gateway_for(runtime)
        payload = await call(gateway, "get_error_log")
        self.assertEqual(payload["error"], "CoreCapabilityUnavailable")
        self.assertFalse(payload["retryable"])
        self.assertEqual(payload["details"], {})
        self.assertEqual(app.calls, [])

    async def test_an_unsupported_core_version_stays_non_retryable(self):
        runtime, _source = await core9.Core20269RuntimeTests()._runtime(
            _snapshot("2099.1.0", evidence=_evidence())
        )
        gateway, app = readmission.gateway_for(runtime)
        payload = await call(gateway, "list_areas")
        self.assertEqual(payload["error"], "CoreCapabilityUnavailable")
        self.assertFalse(payload["retryable"])
        self.assertNotEqual(payload["details"].get("reason_code"), "core_authority_recovering")
        self.assertEqual(app.calls, [])


class _ProjectedRuntime:
    """A Core runtime that admits nothing and reports a fixed recovery state."""

    def __init__(self, recovery=None, *, raises=False):
        self.recovery = recovery
        self.raises = raises

    def acquire(self, *_args, **_kwargs):
        return None

    def route_status(self, _requirements):
        return {"available": False}

    def health_projection(self):
        if self.raises:
            raise RuntimeError("projection unavailable")
        return {"recovery": deepcopy(self.recovery)}


class _NoProjectionRuntime:
    def acquire(self, *_args, **_kwargs):
        return None

    def route_status(self, _requirements):
        return {"available": False}


def recovery(state, *, retired_at=RETIRED, restored_at=None):
    return {
        "recovery_state": state,
        "consecutive_failures": 1,
        "last_failure_reason": "connection_changed",
        "last_failure_detail": None,
        "last_failure_at": retired_at,
        "retired_at": retired_at,
        "last_attempt_at": retired_at,
        "next_recovery_attempt_at": "2026-11-25T15:20:44+00:00",
        "retry_delay_seconds": 30,
        "confirmations": 0,
        "confirmations_required": 2,
        "last_restored_at": restored_at,
    }


class RecoveryStateMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    async def refused(self, runtime) -> dict:
        gateway, app = readmission.gateway_for(runtime)
        payload = await call(gateway, "list_areas")
        self.assertEqual(payload["error"], "CoreCapabilityUnavailable")
        self.assertEqual(app.calls, [])
        return payload

    async def test_open_windows_are_retryable(self):
        for state in ("retrying", "backing_off", "confirming"):
            with self.subTest(state=state):
                payload = await self.refused(_ProjectedRuntime(recovery(state)))
                self.assertTrue(payload["retryable"])
                self.assertEqual(payload["details"]["recovery_state"], state)
                self.assertEqual(payload["details"]["retired_at"], RETIRED)
                self.assertEqual(
                    payload["details"]["next_recovery_attempt_at"],
                    "2026-11-25T15:20:44+00:00",
                )
                self.assertNotIn("retry_delay_seconds", payload["details"])
                self.assertNotIn("last_failure_detail", payload["details"])

    async def test_a_restoration_in_the_same_second_leaves_a_new_window_open(self):
        payload = await self.refused(
            _ProjectedRuntime(recovery("retrying", restored_at=RETIRED))
        )
        self.assertTrue(payload["retryable"])

    async def test_other_causes_keep_the_generic_refusal(self):
        cases = {
            "incompatible": _ProjectedRuntime(recovery("incompatible")),
            "stable": _ProjectedRuntime(recovery("stable")),
            "restored": _ProjectedRuntime(recovery("restored")),
            "initializing": _ProjectedRuntime(recovery("initializing", retired_at=None)),
            "never admitted": _ProjectedRuntime(recovery("backing_off", retired_at=None)),
            "closed by a later restoration": _ProjectedRuntime(
                recovery("retrying", restored_at="2026-11-25T15:20:15+00:00")
            ),
            "malformed retired_at": _ProjectedRuntime(
                recovery("retrying", retired_at="not-a-time")
            ),
            "malformed restored_at": _ProjectedRuntime(
                recovery("retrying", restored_at="not-a-time")
            ),
            "non-dict recovery": _ProjectedRuntime("retrying"),
            "missing recovery": _ProjectedRuntime(None),
            "raising projection": _ProjectedRuntime(raises=True),
            "no projection": _NoProjectionRuntime(),
        }
        for name, runtime in cases.items():
            with self.subTest(case=name):
                payload = await self.refused(runtime)
                self.assertFalse(payload["retryable"])
                self.assertEqual(payload["details"], {})
                self.assertEqual(
                    payload["message"], "The required capability provider is unavailable."
                )


if __name__ == "__main__":
    unittest.main()
