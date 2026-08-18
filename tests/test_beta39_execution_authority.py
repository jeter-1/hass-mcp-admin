"""B39-136-R2/R5: fenced preflight drift and superseded-evidence authority.

Two properties are proven here:

* R2 - the post-lock refresh is a second, fenced source read, and evidence
  that drifted since planning rejects the operation with the approval left
  unconsumed and no provider dispatch.
* R5 - compatibility with a superseded dependency-risk model is readability,
  never execution authority.  An unconsumed superseded plan is non-actionable
  and requires a replan; it is never presented as directly executable.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from tests.f3_operational_fixtures import (  # noqa: E402
    execution_identity,
    make_context,
    make_executor,
    make_plan,
    prepare_context,
)
from ha_mcp_engineering.f3.operational_adapter import (  # noqa: E402
    execute_operational,
)
from ha_mcp_engineering.f3.operational_locks import (  # noqa: E402
    OperationalLockSetCalculator,
)
from ha_mcp_engineering.f3.operational_models import (  # noqa: E402
    SET_INPUT_BOOLEAN_STATE,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS,
    HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
    HELPER_DEPENDENCY_RISK_MODEL,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)


SUPERSEDED_MODEL = "helper-dependency-risk-v2"


def _durable_execution_store_is_usable(root: Path) -> bool:
    """Return whether this platform supports the durable-store fsync path.

    ``DurableExecutionRepository`` fsyncs the containing directory, which
    POSIX supports and Windows does not.  The executor-level cases below skip
    rather than silently weaken when the platform cannot run them; the
    preflight-level cases prove the same four properties everywhere.
    """

    root.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(root, os.O_RDONLY)
    except OSError:
        return False
    os.close(handle)
    return True


class FencedPreflightDriftTests(unittest.IsolatedAsyncioTestCase):
    """R2: all four properties of the fenced final preflight."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_preflight_rejects_post_fence_drift_without_dispatch(self):
        context = make_context(
            self.root / "preflight-drift",
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.synthetic_exact",
            dependency_automation_ids=("porch_light",),
            dependency_drift_on_fenced_read=True,
        )
        prepared = await prepare_context(context)
        locks = context.adapter.lock_requests(prepared)
        strategy = context.adapter.strategies[SET_INPUT_BOOLEAN_STATE]

        result = await context.adapter.preflight(
            prepared, acquired_locks=locks
        )

        # 1. A second, fenced post-lock scan occurred.
        self.assertIn("helper_dependency_fenced_read", context.trace)
        self.assertNotIn("helper_dependency_read", context.trace)
        # 2. The drifted evidence is rejected.
        self.assertFalse(result.eligible)
        self.assertIn("dependency_risk_fingerprint", result.mismatch_fields)
        # 3. The external approval is never consumed.
        self.assertEqual(0, context.approval.consumption_count)
        # 4. No provider dispatch happened.
        self.assertEqual(0, strategy.gateway.provider_dispatches)

    async def test_preflight_accepts_matching_post_fence_evidence(self):
        context = make_context(
            self.root / "preflight-match",
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.synthetic_exact",
            dependency_automation_ids=("porch_light",),
        )
        prepared = await prepare_context(context)
        locks = context.adapter.lock_requests(prepared)

        result = await context.adapter.preflight(
            prepared, acquired_locks=locks
        )

        self.assertIn("helper_dependency_fenced_read", context.trace)
        self.assertTrue(result.eligible)
        self.assertEqual(0, context.approval.consumption_count)

    async def test_drift_observed_after_the_lock_fence_refuses_dispatch(self):
        if not _durable_execution_store_is_usable(self.root / "probe-drift"):
            self.skipTest(
                "durable execution store requires POSIX directory fsync"
            )
        context = make_context(
            self.root / "fenced-drift",
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.synthetic_exact",
            dependency_automation_ids=("porch_light",),
            dependency_drift_on_fenced_read=True,
        )
        prepared = await prepare_context(context)
        executor = make_executor(
            self.root / "fenced-drift", prepared=prepared
        )
        result = await execute_operational(
            executor,
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(),
            approval_consumption=context.approval.consume,
        )
        strategy = context.adapter.strategies[SET_INPUT_BOOLEAN_STATE]

        self.assertIn("helper_dependency_fenced_read", context.trace)
        self.assertNotEqual("succeeded_verified", result.outcome)
        self.assertEqual(0, context.approval.consumption_count)
        self.assertEqual(0, result.dispatch_count)
        self.assertEqual(0, strategy.gateway.provider_dispatches)

    async def test_matching_evidence_after_the_fence_dispatches_once(self):
        if not _durable_execution_store_is_usable(self.root / "probe-match"):
            self.skipTest(
                "durable execution store requires POSIX directory fsync"
            )
        context = make_context(
            self.root / "fenced-match",
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.synthetic_exact",
            dependency_automation_ids=("porch_light",),
        )
        prepared = await prepare_context(context)
        executor = make_executor(
            self.root / "fenced-match", prepared=prepared
        )
        result = await execute_operational(
            executor,
            adapter=context.adapter,
            prepared=prepared,
            identity=execution_identity(),
            approval_consumption=context.approval.consume,
        )
        strategy = context.adapter.strategies[SET_INPUT_BOOLEAN_STATE]

        self.assertIn("helper_dependency_fenced_read", context.trace)
        self.assertEqual("succeeded_verified", result.outcome)
        self.assertEqual(1, result.dispatch_count)
        self.assertEqual(1, strategy.gateway.provider_dispatches)


class SupersededEvidenceAuthorityTests(unittest.TestCase):
    """R5: compatibility is readability, not execution authority."""

    def test_model_sets_are_distinct_and_ordered(self):
        self.assertIn(
            HELPER_DEPENDENCY_RISK_MODEL,
            HELPER_DEPENDENCY_RISK_EXECUTION_MODELS,
        )
        self.assertIn(
            SUPERSEDED_MODEL, HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS
        )
        self.assertNotIn(
            SUPERSEDED_MODEL, HELPER_DEPENDENCY_RISK_EXECUTION_MODELS
        )
        self.assertTrue(
            HELPER_DEPENDENCY_RISK_EXECUTION_MODELS.issubset(
                HELPER_DEPENDENCY_RISK_COMPATIBLE_MODELS
            )
        )

    def test_superseded_plan_is_not_actionable_and_requires_replan(self):
        plan = make_plan(
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.synthetic_exact",
            dependency_automation_ids=("porch_light",),
            dependency_risk_model=SUPERSEDED_MODEL,
            dependency_lock_projection=False,
        )
        self.assertFalse(
            ChangeGovernanceService._helper_dependency_plan_is_actionable(
                plan
            )
        )
        self.assertTrue(
            ChangeGovernanceService._helper_dependency_replan_required(plan)
        )

    def test_current_model_plan_is_actionable_and_needs_no_replan(self):
        plan = make_plan(
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.synthetic_exact",
            dependency_automation_ids=("porch_light",),
        )
        self.assertTrue(
            ChangeGovernanceService._helper_dependency_plan_is_actionable(
                plan
            )
        )
        self.assertFalse(
            ChangeGovernanceService._helper_dependency_replan_required(plan)
        )

    def test_durable_intent_is_recognised_for_recovery(self):
        plan = make_plan(
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.synthetic_exact",
            dependency_risk_model=SUPERSEDED_MODEL,
            dependency_lock_projection=False,
        )
        self.assertFalse(
            ChangeGovernanceService._operational_intent_is_durable(plan)
        )
        plan.operational.dispatch["dispatched"] = True
        plan.operational.dispatch["attempt_count"] = 1
        self.assertTrue(
            ChangeGovernanceService._operational_intent_is_durable(plan)
        )
        # A post-intent superseded record stays readable and recoverable; only
        # new execution authority is withheld.
        self.assertTrue(
            ChangeGovernanceService._helper_dependency_replan_required(plan)
        )


class SupersededLockProjectionTests(unittest.IsolatedAsyncioTestCase):
    """A binding without current execution authority projects no locks."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def _prepared(self, name: str, **kwargs):
        context = make_context(
            self.root / name,
            SET_INPUT_BOOLEAN_STATE,
            target_id="input_boolean.synthetic_exact",
            dependency_automation_ids=("porch_light",),
            **kwargs,
        )
        return await prepare_context(context)

    async def test_superseded_binding_yields_no_lock_projection(self):
        prepared = await self._prepared(
            "superseded",
            dependency_risk_model=SUPERSEDED_MODEL,
            dependency_lock_projection=False,
        )
        with self.assertRaises(ValueError) as caught:
            OperationalLockSetCalculator().calculate(prepared)
        self.assertIn("not executable", str(caught.exception))

    async def test_current_binding_without_projection_also_fails_closed(self):
        prepared = await self._prepared(
            "no-projection", dependency_lock_projection=False
        )
        with self.assertRaises(ValueError) as caught:
            OperationalLockSetCalculator().calculate(prepared)
        self.assertIn("projection is invalid", str(caught.exception))

    async def test_current_binding_with_projection_produces_locks(self):
        prepared = await self._prepared("current")
        requests = OperationalLockSetCalculator().calculate(prepared)
        self.assertTrue(
            any(
                request.key.startswith("helper_dependency:")
                for request in requests
            )
        )


if __name__ == "__main__":
    unittest.main()
