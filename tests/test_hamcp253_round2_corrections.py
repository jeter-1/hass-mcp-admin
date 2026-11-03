"""HAMCP-253 round-2 corrections (Codex review of PR #354).

R2-M1: the health identification rows screen a stored plan identifier with
the configured values, as the acknowledgement bucket does. A configured
value shaped like a record identifier never reaches health.

R2-M2: an acknowledgement whose audit destination is known to be unusable
is refused before the acknowledgement namespace or ledger changes.

R2-M3: a decision whose persistence cannot be excluded is reported as
pending, never as refused, so a refusal never later takes effect. The same
holds for the completion that settles it.

Fixtures come from the real governance code paths named in
``tests/test_hamcp253_record_acknowledgement.py``; damaged records are
direct edits of those files, each named where it is made. All values are
synthetic and offline, and every Home Assistant entry point is patched to
fail the test if it is reached.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.governance.runtime import GovernanceRuntime  # noqa: E402
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.health import HealthRegistry  # noqa: E402
from ha_mcp_engineering.tools import (  # noqa: E402
    compatibility,
    get_registered_server,
    governance as governance_tools,
    registered_tools,
)
from tests.test_hamcp253_record_acknowledgement import (  # noqa: E402
    SENSITIVE_VALUES,
    no_home_assistant,
)
from tests.test_hamcp253_record_acknowledgement_web import (  # noqa: E402
    Hamcp253IngressCase,
    ingress_client,
)
from tests.test_hamcp253_round1_corrections import (  # noqa: E402
    UNRECOGNIZED,
    rewrite_json,
)


# Configured, and shaped exactly like a record identifier, so that only the
# configured-value screen (not the identifier pattern) can withhold it.
HEX_SECRET = "a253" * 8
HEX_SECRETS = (HEX_SECRET, "b253" * 8, "c253" * 8)


# -- R2-M1 ------------------------------------------------------------------


class HealthIdentificationScreensConfiguredValuesTests(Hamcp253IngressCase):
    def build_service(self, repository):
        # Hamcp253Case.build_service with one more configured value.
        return ChangeGovernanceService(
            repository,
            self.gateway,
            AuditLogger(str(self.audit_path), "test-access-secret-value"),
            now=self.clock,
            sensitive_values=(*SENSITIVE_VALUES, *HEX_SECRETS),
        )

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.service = self.build_service(self.repository)

    async def outputs(self, record_ids):
        """Every output that can name the records, as text."""

        outputs = {
            "health": json.dumps(self.service.health_summary()),
            "stuck list": json.dumps(self.service.stuck_governance_records()),
        }
        runtime = GovernanceRuntime()
        runtime.service = self.service
        inspect_tool = registered_tools(get_registered_server())[
            "inspect_governance_record"
        ]
        with (
            patch.object(compatibility, "HEALTH", HealthRegistry(governance=runtime)),
            patch.object(compatibility, "rest", AsyncMock()) as rest,
            patch.object(compatibility, "ws_command", AsyncMock()) as websocket,
            patch.object(governance_tools.GOVERNANCE, "require", return_value=self.service),
        ):
            outputs["mcp get_server_health"] = await compatibility.get_server_health(
                check_ha=False
            )
            for record_id in record_ids:
                outputs[f"inspection {record_id}"] = json.dumps(
                    self.inspect_record("change_plan", record_id)
                )
                outputs[f"mcp inspect {record_id}"] = await inspect_tool.run(
                    {"record_kind": "change_plan", "record_id": record_id}
                )
        self.assertEqual(rest.await_count + websocket.await_count, 0)
        async with ingress_client(self.mount()) as client:
            for path in ("/records", *map(self.review_path, record_ids)):
                response = await client.get(path)
                self.assertEqual(response.status_code, 200)
                outputs[f"ingress {path}"] = response.text
        outputs["audit"] = (
            self.audit_path.read_text(encoding="utf-8")
            if self.audit_path.exists() else ""
        )
        return outputs

    def assert_listed(self, health, expected):
        """``expected`` maps each stored identifier to the value shown."""

        self.assertEqual(health["projection_failure_count"], len(expected))
        self.assertIs(health["projection_failures_truncated"], False)
        # Ordered by stored identifier, as before; only the shown value of a
        # rejected identifier is a fixed marker.
        self.assertEqual(
            [row["plan_id"] for row in health["projection_failures"]],
            [expected[stored] for stored in sorted(expected)],
        )
        self.assertEqual(
            {row["category"] for row in health["projection_failures"]},
            {"policy_subject_hash_not_reproducible"},
        )

    def assert_never_echoed(self, outputs, normal):
        for where, text in outputs.items():
            for secret in HEX_SECRETS:
                self.assertNotIn(secret, text, f"{where} echoed a configured value")
        for where in ("health", "ingress /records", "audit"):
            self.assertIn(normal, outputs[where], where)

    async def test_configured_hex_value_stored_as_a_plan_identifier(self):
        damaged, normal = await self.projection_failed_plans(count=2)
        path = self.repository._path(damaged)
        # The damage: a real policy_snapshot_mismatch record whose stored
        # identifier is replaced by a configured value of the same shape.
        rewrite_json(path, lambda data: data.update(plan_id=HEX_SECRET))
        self.restart()
        self.assertIn(HEX_SECRET, path.read_text(encoding="utf-8"))
        with no_home_assistant(self):
            self.assertEqual(
                self.inspect_record("change_plan", normal)["failure"]["reason_code"],
                "policy_snapshot_mismatch",
            )
            self.assert_listed(
                self.service.health_summary(),
                {HEX_SECRET: UNRECOGNIZED, normal: normal},
            )
            outcome = self.acknowledge("change_plan", normal)
            self.assertEqual(outcome["status"], "acknowledged")
            outputs = await self.outputs([damaged, normal])
        self.assert_never_echoed(outputs, normal)
        # The MCP health scan refuses a record stored under another
        # identifier before it lists anything (an existing storage check).
        self.assertEqual(
            json.loads(outputs["mcp get_server_health"])["error_code"],
            "change_plan_storage_error",
        )

    async def test_configured_hex_value_as_a_consistent_record_identifier(self):
        damaged, normal = await self.projection_failed_plans(count=2)
        # The damage: the same record stored consistently under a configured
        # value, so that every health scan, the MCP one included, lists it.
        old_path = self.repository._path(damaged)
        rewrite_json(old_path, lambda data: data.update(plan_id=HEX_SECRET))
        old_path.rename(self.repository._path(HEX_SECRET))
        self.restart()
        with no_home_assistant(self):
            self.assert_listed(
                self.service.health_summary(),
                {HEX_SECRET: UNRECOGNIZED, normal: normal},
            )
            outcome = self.acknowledge("change_plan", normal)
            self.assertEqual(outcome["status"], "acknowledged")
            outputs = await self.outputs([normal])
        self.assert_never_echoed(outputs, normal)
        mcp_health = json.loads(outputs["mcp get_server_health"])
        self.assertIs(mcp_health["success"], True)
        self.assert_listed(
            mcp_health["data"]["governance"],
            {HEX_SECRET: UNRECOGNIZED, normal: normal},
        )

    async def test_the_marker_preserves_counts_order_and_bounds(self):
        ids = await self.projection_failed_plans(count=9)
        stored = dict(zip(ids, ids))
        for plan_id, secret in zip(ids[:3], HEX_SECRETS):
            rewrite_json(
                self.repository._path(plan_id),
                lambda data, secret=secret: data.update(plan_id=secret),
            )
            stored[plan_id] = secret
        self.restart()
        with no_home_assistant(self):
            health = self.service.health_summary()
        shown = [
            UNRECOGNIZED if value in HEX_SECRETS else value
            for value in sorted(stored.values())
        ]
        self.assertEqual(health["projection_failure_count"], 9)
        self.assertIs(health["projection_failures_truncated"], True)
        self.assertEqual(
            [row["plan_id"] for row in health["projection_failures"]], shown[:8]
        )
        text = json.dumps(health)
        for secret in HEX_SECRETS:
            self.assertNotIn(secret, text)

if __name__ == "__main__":
    unittest.main()
