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
                "--engineering-endpoint",
                "http://127.0.0.1:18100/synthetic/mcp",
                "--fixture-stats-url",
                "http://127.0.0.1:18123/__fixture__/stats",
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


if __name__ == "__main__":
    unittest.main()
