"""Reconcile Core authority in tests the way production does (HAMCP-215 H1).

A freshly configured ``CoreRuntime`` may admit on its boot sequence
(``startup``, then ``startup_retry``) or on its first background verification.
Every other non-admitted state (retired, incompatible, withheld until a signed
profile arrives, never initialized after boot) passes the one restoration gate:
consecutive complete background verifications agreeing on identity, version
and reviewed contracts. On-demand triggers never admit, so tests that expect
admission use a boot trigger on a fresh runtime, or this helper afterwards.
"""

from __future__ import annotations

from typing import Any

from ha_mcp_engineering.ha_core_readmission.runtime import (
    CORE_RECOVERY_FLAP_CONFIRMATIONS,
)


async def reconcile_through_gate(core: Any, trigger: str = "periodic") -> dict[str, Any]:
    """Run background verifications while the gate is confirming.

    Stops at the first snapshot that is not confirming (admitted, failed or
    still withheld), bounded by the flap confirmation count, and returns it.
    No extra verification runs once the outcome is known.
    """

    snapshot = await core.reconcile_once(trigger)
    for _ in range(CORE_RECOVERY_FLAP_CONFIRMATIONS - 1):
        if snapshot["recovery"]["recovery_state"] != "confirming":
            break
        snapshot = await core.reconcile_once(trigger)
    return snapshot
