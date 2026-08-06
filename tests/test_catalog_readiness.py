import asyncio
from contextlib import redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.application import (  # noqa: E402
    _supervise_upstream_reconciliation,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalState,
    PlanStatus,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    FakeLifecycleGateway,
    LegacyGateway,
)


ACCEPTANCE_PATH = ROOT / "scripts" / "exact_image_read_gateway_acceptance.py"
acceptance_spec = importlib.util.spec_from_file_location(
    "exact_image_read_gateway_acceptance_test_module",
    ACCEPTANCE_PATH,
)
acceptance = importlib.util.module_from_spec(acceptance_spec)
assert acceptance_spec.loader is not None
acceptance_spec.loader.exec_module(acceptance)


class ApplicationCatalogReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_reconcile_completes_before_catalog_is_marked_ready(self):
        order = []
        initial = {
            "configured": True,
            "reconciliation_status": "admitted",
        }
        supervised_snapshots = []

        class Gateway:
            initial_catalog_reconciliation_required = True

            def mark_initial_catalog_reconciled(self):
                order.append("ready")

        async def reconcile(_server):
            order.append("reconcile")
            return initial

        async def supervise(_server, *, initial_snapshot=None):
            order.append("supervise")
            supervised_snapshots.append(initial_snapshot)

        with patch(
            "ha_mcp_engineering.application.UPSTREAM_READ_GATEWAY.reconcile_until_initialized",
            side_effect=reconcile,
        ), patch(
            "ha_mcp_engineering.application.UPSTREAM_READ_GATEWAY.supervise_reconciliation",
            side_effect=supervise,
        ):
            await _supervise_upstream_reconciliation(Gateway())

        self.assertEqual(order, ["reconcile", "ready", "supervise"])
        self.assertEqual(supervised_snapshots, [initial])

    async def test_failed_initial_reconcile_never_marks_catalog_ready(self):
        marked = False

        class Gateway:
            initial_catalog_reconciliation_required = True

            def mark_initial_catalog_reconciled(self):
                nonlocal marked
                marked = True

        with patch(
            "ha_mcp_engineering.application.UPSTREAM_READ_GATEWAY.reconcile_until_initialized",
            side_effect=RuntimeError("synthetic reconciliation failure"),
        ), patch(
            "ha_mcp_engineering.application.UPSTREAM_READ_GATEWAY.supervise_reconciliation"
        ) as supervise:
            with self.assertRaisesRegex(
                RuntimeError,
                "synthetic reconciliation failure",
            ):
                await _supervise_upstream_reconciliation(Gateway())

        self.assertFalse(marked)
        supervise.assert_not_called()


class ExactImageDiagnosticTests(unittest.TestCase):
    def test_operational_errors_are_excluded_from_success_accounting(self):
        total_calls = (
            len(acceptance.DELEGATED_READ_CALLS)
            + 1
            + len(acceptance.UPSTREAM_ERROR_CALLS)
        )

        self.assertEqual(acceptance.EXPECTED_OPERATIONAL_ERROR_CALLS, 2)
        self.assertEqual(
            acceptance.expected_successful_delegated_calls(total_calls),
            total_calls - 2,
        )
        self.assertEqual(
            acceptance.EXPECTED_OUTCOME_CATEGORY_COUNTS,
            {
                "upstream_error": 2,
                "invalid_request": 1,
                "entity_not_found": 2,
                "automation_not_found": 1,
            },
        )
        self.assertEqual(
            acceptance.EXPECTED_LAST_CALL_FAILURE_CATEGORY,
            None,
        )

    def test_catalog_failure_diagnostics_are_bounded_and_whitelisted(self):
        secret = "synthetic-secret-that-must-not-be-emitted"
        diagnostics = acceptance._bounded_catalog_diagnostics(
            {
                "upstream_read_gateway": {
                    "configured": True,
                    "reconciliation_status": "degraded",
                    "recommended_action": "Review the quarantined contract.",
                    "failure_counts": {"schema_mismatch": 2},
                    "quarantined_tools": [
                        {
                            "upstream_name": "ha_search",
                            "reason": "input_schema_mismatch",
                            "expected_fingerprint": "a" * 64,
                            "observed_fingerprint": "b" * 64,
                            "raw_descriptor": secret,
                        }
                    ],
                    "credential_material": secret,
                }
            },
            expected_names={"native", "ha_search"},
            observed_names={"native"},
            readiness={"ready": True, "http_status": 200},
        )
        encoded = json.dumps(diagnostics, sort_keys=True)

        self.assertEqual(
            diagnostics["missing_expected_tools"], ["ha_search"]
        )
        self.assertEqual(
            diagnostics["upstream_read_gateway"]["failure_counts"],
            {"schema_mismatch": 2},
        )
        quarantine = diagnostics["upstream_read_gateway"][
            "quarantined_tools"
        ][0]
        self.assertEqual(quarantine["expected_fingerprint"], "a" * 64)
        self.assertEqual(quarantine["observed_fingerprint"], "b" * 64)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("credential_material", encoded)
        self.assertNotIn("raw_descriptor", encoded)

    def test_exception_group_writes_bounded_failure_artifact(self):
        async def fail(_args):
            raise ExceptionGroup(
                "transport wrapper",
                [
                    RuntimeError("synthetic-sensitive-value"),
                    acceptance.AcceptanceFailure(
                        "An exact matched reviewed read is missing.",
                        diagnostics={
                            "missing_expected_tools": ["ha_get_state"],
                            "upstream_read_gateway": {
                                "reconciliation_status": "waiting",
                            },
                        },
                    ),
                ],
            )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            argv = [
                "exact_image_read_gateway_acceptance.py",
                "--upstream-endpoint",
                "http://127.0.0.1:18086/synthetic/mcp",
                "--configured-upstream-endpoint",
                "http://abcdef12-ha-mcp:18086/synthetic/mcp",
                "--expected-upstream-version",
                "7.14.1",
                "--engineering-endpoint",
                "http://127.0.0.1:18100/synthetic/mcp",
                "--fixture-stats-url",
                "http://127.0.0.1:18123/__fixture__/stats",
                "--ha-url",
                "http://127.0.0.1:18123",
                "--ha-token",
                "synthetic-read-gateway-token",
                "--output",
                str(output),
            ]
            with patch.object(acceptance, "run", side_effect=fail), patch.object(
                sys,
                "argv",
                argv,
            ), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    acceptance.main()
            result = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(
            result["failure"]["category"],
            "acceptance_failure",
        )
        self.assertIn(
            "AcceptanceFailure",
            result["failure"]["exception_types"],
        )
        self.assertEqual(
            result["diagnostics"]["missing_expected_tools"],
            ["ha_get_state"],
        )
        self.assertNotIn(
            "synthetic-sensitive-value",
            json.dumps(result, sort_keys=True),
        )

    def test_governance_failure_artifact_includes_only_bounded_error_code(self):
        result = acceptance._bounded_failure_result(
            GovernanceError(
                ErrorCode.APPROVAL_SEQUENCE_FAILURE,
                details={"resource_id": "must-not-be-exported"},
            )
        )

        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(
            result["failure"]["category"],
            "acceptance_execution_failure",
        )
        self.assertEqual(
            result["failure"]["error_code"],
            "approval_sequence_failure",
        )
        self.assertEqual(result["diagnostics"], {})
        self.assertNotIn("must-not-be-exported", json.dumps(result))

    def test_ci_waits_for_ready_200_and_always_uploads_result(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        engineering_start = workflow.split(
            "- name: Build and start Engineering through its production discovery path",
            1,
        )[1].split(
            "- name: Run exact-image authenticated gateway acceptance",
            1,
        )[0]
        acceptance_step = workflow.split(
            "- name: Run exact-image authenticated gateway acceptance",
            1,
        )[1].split(
            "- name: Upload bounded exact-image result",
            1,
        )[0]
        upload_step = workflow.split(
            "- name: Upload bounded exact-image result",
            1,
        )[1].split(
            "- name: Remove disposable exact-image environment",
            1,
        )[0]

        self.assertIn("--write-out '%{http_code}'", engineering_start)
        self.assertIn("http://127.0.0.1:18100/ready", engineering_start)
        self.assertIn('test "$status" = "200"', engineering_start)
        self.assertNotIn('test "$status" != "000"', engineering_start)
        self.assertIn("if: always()", acceptance_step)
        self.assertIn("if: always()", upload_step)
        self.assertIn("if-no-files-found: error", upload_step)


class ExactImageAuthorityFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = ChangePlanRepository(
            Path(self.temp.name) / "plans"
        )
        self.lifecycle = FakeLifecycleGateway()
        self.service = ChangeGovernanceService(
            self.repository,
            LegacyGateway(),
            lifecycle_gateway=self.lifecycle,
            now=self.lifecycle.now,
        )
        self.telemetry, self.context = begin_request(
            "exact-image-authority-fixture"
        )
        self.telemetry.caller_id = "exact-image-acceptance-caller"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def test_canonical_standard_authority_v3_bundle_validates(self):
        proposal = await self.service.create_reload_plan(
            reload_target="automation"
        )
        granted = await acceptance._grant_authority_v3_bundle(
            self.service,
            proposal["plan"],
        )
        plan = self.repository.get(proposal["plan"]["plan_id"])

        self.assertEqual(granted["status"], "approved")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.service._require_policy_snapshot(plan)
        self.assertEqual(plan.approval.authority_version, 3)
        self.assertEqual(plan.approval.bundle_state, "fully_approved")
        self.assertEqual(plan.approval.state, ApprovalState.APPROVED)
        self.assertIsNone(
            plan.approval.elevated_risk_acknowledgement
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)
        self.assertIsNone(
            self.service.task_repository.get_for_plan(plan.plan_id)
        )

    async def test_canonical_authority_v3_seed_is_valid_and_dispatch_free(self):
        proposal = await self.service.create_addon_restart_plan(
            addon_slug="local_test_addon"
        )
        seeded = await acceptance._seed_dispatched_lifecycle_recovery(
            self.service,
            proposal["plan"],
        )
        plan = self.repository.get(seeded["plan_id"])
        task = self.service.task_repository.get_for_plan(seeded["plan_id"])

        self.assertIsNotNone(plan)
        self.assertIsNotNone(task)
        assert plan is not None and task is not None
        self.service._require_policy_snapshot(plan)
        self.assertEqual(plan.approval.authority_version, 3)
        self.assertEqual(plan.approval.state, ApprovalState.CONSUMED)
        self.assertEqual(plan.approval.bundle_state, "consumed")
        self.assertTrue(plan.approval.same_principal_confirmed)
        self.assertEqual(plan.status, PlanStatus.VERIFICATION_REQUIRED)
        acknowledgement = plan.approval.elevated_risk_acknowledgement
        self.assertIsNotNone(acknowledgement)
        assert acknowledgement is not None
        self.assertEqual(acknowledgement.state, ApprovalState.CONSUMED)
        self.assertEqual(
            acknowledgement.approver_principal,
            plan.approval.approver_principal,
        )
        self.assertEqual(task.task_schema_version, 1)
        self.assertEqual(task.state.value, "observing")
        self.assertEqual(len(task.provider_attempts), 1)
        self.assertEqual(
            [event.event_type for event in task.events],
            [
                "task_created",
                "preflight_started",
                "approval_consumed",
                "dispatch_attempted",
                "provider_response_recorded",
            ],
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)

    async def test_incomplete_consumed_authority_v3_seed_fails_closed(self):
        proposal = await self.service.create_addon_restart_plan(
            addon_slug="local_test_addon"
        )
        plan = self.repository.get(proposal["plan"]["plan_id"])
        self.assertIsNotNone(plan)
        assert plan is not None and plan.operational is not None
        plan.approval.state = ApprovalState.CONSUMED
        plan.status = PlanStatus.VERIFICATION_REQUIRED
        plan.operational.dispatch.update(
            {
                "attempt_count": 1,
                "dispatched": True,
                "provider_response_received": True,
            }
        )
        self.repository.save(plan)

        with self.assertRaises(GovernanceError) as error:
            self.service.resolved_plans()
        self.assertEqual(
            error.exception.code,
            ErrorCode.APPROVAL_SEQUENCE_FAILURE,
        )
        self.assertEqual(self.lifecycle.dispatch_count, 0)


if __name__ == "__main__":
    unittest.main()
