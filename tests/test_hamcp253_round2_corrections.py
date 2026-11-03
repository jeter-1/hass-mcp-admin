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
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.governance import (  # noqa: E402
    record_acknowledgement as ledger_module,
)
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
    PROJECTION_WARNING,
    SENSITIVE_VALUES,
    Hamcp253Case,
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


# -- R2-M2 ------------------------------------------------------------------


AUDIT_SECRET = "test-access-secret-value"
NOT_ROOT = os.geteuid() != 0


class AuditDestinationTests(Hamcp253Case):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        (self.target,) = await self.projection_failed_plans()
        self.destinations = self.audit_path.parent / "destinations"
        self.destinations.mkdir()
        self.working_audit = self.service.audit

    def namespace(self):
        """Every path under the acknowledgement namespace, with its bytes."""

        root = self.repository.root / ledger_module.MODEL
        if not root.exists():
            return None
        return {
            str(path.relative_to(root)): (
                "directory" if path.is_dir() else path.read_bytes()
            )
            for path in sorted(root.rglob("*"))
        }

    def read_only(self, path):
        path.chmod(0o500 if path.is_dir() else 0o400)
        self.restore.append(path)

    def unusable(self, name):
        """An audit logger whose destination cannot take an event."""

        base = self.destinations / f"{len(list(self.destinations.iterdir()))} {name}"
        base.mkdir()
        if name == "missing logger":
            return None
        if name == "disabled logger":
            return AuditLogger(str(base / "audit.jsonl"), AUDIT_SECRET, enabled=False)
        if name == "directory":
            (base / "audit.jsonl").mkdir()
        elif name == "lock directory":
            (base / "audit.jsonl").touch()
            (base / "audit.jsonl.lock").mkdir()
        elif name == "missing parent":
            return AuditLogger(str(base / "absent" / "audit.jsonl"), AUDIT_SECRET)
        elif name == "non-writable file":
            (base / "audit.jsonl").write_bytes(b"")
            self.read_only(base / "audit.jsonl")
        elif name == "non-writable parent":
            self.read_only(base)
        return AuditLogger(str(base / "audit.jsonl"), AUDIT_SECRET)

    DESTINATIONS = (
        "directory",
        "lock directory",
        "missing parent",
        *(("non-writable file", "non-writable parent") if NOT_ROOT else ()),
        "disabled logger",
        "missing logger",
    )

    def assert_refused_unchanged(self, requests):
        for destination in self.DESTINATIONS:
            for overrides in requests:
                with self.subTest(destination=destination, **overrides):
                    self.restore = []
                    self.service.audit = self.unusable(destination)
                    before = self.namespace()
                    with no_home_assistant(self):
                        outcome = self.acknowledge(
                            "change_plan", self.target, **overrides
                        )
                    self.assertEqual(outcome["status"], "refused")
                    self.assertEqual(outcome["refusal_reason"], "audit_unavailable")
                    self.assertIsNone(outcome["ledger_sequence"])
                    self.assertIs(outcome["audit_recorded"], False)
                    self.assertEqual(self.namespace(), before)
                for path in self.restore:
                    path.chmod(0o700)
                self.service.audit = self.working_audit

    async def test_fresh_namespace_is_never_created(self):
        self.assert_refused_unchanged(({},))
        self.assertIsNone(self.namespace())
        health = self.service.health_summary()
        self.assertEqual(health["projection_failure_warning"], PROJECTION_WARNING)
        self.assertEqual(self.bucket(health)["unacknowledged_ids"], [self.target])

    async def test_existing_ledger_is_byte_unchanged(self):
        self.assert_success(self.acknowledge("change_plan", self.target), "acknowledged")
        ledger = self.ledger_path().read_bytes()
        self.assert_refused_unchanged((
            {"classification": "current_defect"},
            {"action": "revoke", "classification": None},
        ))
        self.assertEqual(self.ledger_path().read_bytes(), ledger)
        self.assertEqual(self.completions(), {1: "recorded"})
        view = self.inspect_record("change_plan", self.target)
        self.assertEqual(view["acknowledgement"]["classification"], "historical_record")

    async def test_a_new_file_in_a_usable_directory_is_accepted(self):
        # The control: the check refuses unusable destinations, not new ones.
        path = self.destinations / "new" / "audit.jsonl"
        path.parent.mkdir()
        self.service.audit = AuditLogger(str(path), AUDIT_SECRET)
        with no_home_assistant(self):
            outcome = self.acknowledge("change_plan", self.target)
        self.assert_success(outcome, "acknowledged")
        self.assertIs(outcome["audit_recorded"], True)
        self.assertIn(self.target, path.read_text(encoding="utf-8"))

    async def test_a_destination_lost_after_the_check_abandons_the_decision(self):
        # The destination passes the check, then becomes a directory before
        # the decision's audit write: the R1-M3 recovery still applies.
        path = self.destinations / "lost" / "audit.jsonl"
        path.parent.mkdir()
        self.service.audit = AuditLogger(str(path), AUDIT_SECRET)

        def lose_destination(stage):
            if stage == "before_write" and not path.is_dir():
                path.unlink(missing_ok=True)
                path.mkdir()

        self.service.acknowledgements.fault_hook = lose_destination
        with no_home_assistant(self):
            outcome = self.acknowledge("change_plan", self.target)
        self.service.acknowledgements.fault_hook = None
        self.assertEqual(outcome["status"], "refused")
        self.assertEqual(outcome["refusal_reason"], "audit_write_failed")
        self.assertIs(outcome["audit_recorded"], False)
        (decision,) = self.decisions()
        self.assertEqual(self.completions(), {decision["sequence"]: "abandoned"})
        view = self.inspect_record("change_plan", self.target)
        self.assertEqual(view["acknowledgement"]["state"], "unacknowledged")
        self.assertIs(view["acknowledgement"]["audit_pending"], False)


if __name__ == "__main__":
    unittest.main()
