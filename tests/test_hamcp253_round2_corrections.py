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

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
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

    async def test_list_change_plans_screens_the_stored_identifier(self):
        # Josh's bounded inclusion (2026-11-03): list_change_plans uses the
        # same screen for its projection-failure rows.
        damaged, normal = await self.projection_failed_plans(count=2)
        # The damage: as above, a record stored consistently under a
        # configured value.
        old_path = self.repository._path(damaged)
        rewrite_json(old_path, lambda data: data.update(plan_id=HEX_SECRET))
        old_path.rename(self.repository._path(HEX_SECRET))
        self.restart()
        list_tool = registered_tools(get_registered_server())["list_change_plans"]
        with no_home_assistant(self), patch.object(
            governance_tools.GOVERNANCE, "require", return_value=self.service
        ):
            listed = self.service.list_plans()
            text = await list_tool.run({})
        for where, output in (("service", json.dumps(listed)), ("mcp", text)):
            with self.subTest(where=where):
                for secret in HEX_SECRETS:
                    self.assertNotIn(secret, output)
                self.assertIn(normal, output)
        self.assertEqual(listed["projection_failure_count"], 2)
        self.assertIs(listed["projection_failures_truncated"], False)
        self.assertEqual(
            [row["plan_id"] for row in listed["projection_failures"]],
            [UNRECOGNIZED if stored == HEX_SECRET else stored
             for stored in sorted((HEX_SECRET, normal))],
        )
        self.assertEqual(
            {row["error_code"] for row in listed["projection_failures"]},
            {"policy_snapshot_mismatch"},
        )
        mcp = json.loads(text)
        self.assertIs(mcp["success"], True)
        self.assertEqual(
            mcp["data"]["projection_failures"], listed["projection_failures"]
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



# -- R2-M3 ------------------------------------------------------------------


PENDING = "acknowledgement_audit_reconciliation_pending"
# Each is reached only after os.replace has made the new ledger visible.
AFTER_REPLACEMENT = ("after_replace", "directory_open", "directory_fsync")


class PersistenceUncertaintyTests(Hamcp253Case):
    """Failures after the ledger replacement, for each write of a request.

    ``kept`` continues with the replaced ledger. ``lost`` puts back the
    ledger bytes from before the failed write, as a crash could when the
    directory entry was never made durable.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.targets = iter(await self.projection_failed_plans(count=7))

    def audit_bytes(self):
        return self.audit_path.read_bytes() if self.audit_path.exists() else b""

    def decision_for(self, target):
        found = [d for d in self.decisions() if d["body"]["record_id"] == target]
        return found[-1] if found else None

    def decision_audits(self, decision):
        identifier = ledger_module.audit_event_id(decision)
        return [entry for entry in self.acknowledgement_audits()
                if entry.get("audit_event_id") == identifier]

    def state_of(self, target):
        view = self.inspect_record("change_plan", target)["acknowledgement"]
        return view["state"], view["audit_pending"]

    @contextmanager
    def failing_write(self, write, stage):
        """Fail ledger write number ``write`` of one request at ``stage``."""

        store = self.service.acknowledgements
        state = {"writes": 0, "before": None, "failed": 0}
        real_open, real_fsync = os.open, os.fsync

        def armed():
            return state["writes"] == write

        def fail(what):
            state["failed"] += 1
            raise OSError(f"synthetic failure: {what}")

        def hook(name):
            if name == "before_write":
                state["writes"] += 1
                if armed() and self.ledger_path().exists():
                    state["before"] = self.ledger_path().read_bytes()
            elif name == stage and armed():
                fail(name)

        def ledger_directory_open(path, flags, *args, **kwargs):
            if (stage == "directory_open" and armed() and flags & os.O_DIRECTORY
                    and Path(path) == store.root):
                fail("directory open")
            return real_open(path, flags, *args, **kwargs)

        def ledger_directory_fsync(fd):
            # Only a directory: the temporary file and the audit log still
            # sync normally.
            if (stage == "directory_fsync" and armed()
                    and stat.S_ISDIR(os.fstat(fd).st_mode)):
                fail("directory fsync")
            return real_fsync(fd)

        store.fault_hook = hook
        try:
            with patch.object(ledger_module.os, "open", ledger_directory_open), \
                    patch.object(ledger_module.os, "fsync", ledger_directory_fsync):
                yield state
        finally:
            store.fault_hook = None
        self.assertEqual(state["failed"], 1, "the failure was not injected")

    def lose_replacement(self, state):
        if state["before"] is None:
            self.ledger_path().unlink()
        else:
            self.ledger_path().write_bytes(state["before"])

    def assert_converged(self):
        """Nothing pending, no duplicate, nothing effective without audit."""

        completions = self.completions()
        for decision in self.decisions():
            audits = self.decision_audits(decision)
            self.assertLessEqual(len(audits), 1)
            self.assertIn(decision["sequence"], completions)
            if completions[decision["sequence"]] == "recorded":
                self.assertEqual(len(audits), 1)
        health = self.service.health_summary()
        self.assertEqual(health["record_acknowledgement"]["audit_pending_count"], 0)

    def restart_and_converge(self):
        self.restart()
        self.assert_converged()
        ledger, audit = self.ledger_path().read_bytes(), self.audit_bytes()
        # A second start finds nothing left to do.
        self.restart()
        self.assertEqual(self.ledger_path().read_bytes(), ledger)
        self.assertEqual(self.audit_bytes(), audit)

    def assert_pending(self, outcome, decision):
        self.assertEqual(outcome["status"], "pending")
        self.assertEqual(outcome["refusal_reason"], PENDING)
        self.assertIsNotNone(decision)
        self.assertEqual(outcome["ledger_sequence"], decision["sequence"])

    async def test_decision_write_failing_after_replacement(self):
        for stage in AFTER_REPLACEMENT:
            for branch in ("kept", "lost"):
                with self.subTest(stage=stage, branch=branch), no_home_assistant(self):
                    target, before = next(self.targets), self.audit_bytes()
                    with self.failing_write(1, stage) as state:
                        outcome = self.acknowledge("change_plan", target)
                    decision = self.decision_for(target)
                    # Stored without a completion: pending, not refused.
                    self.assert_pending(outcome, decision)
                    self.assertNotIn(decision["sequence"], self.completions())
                    self.assertEqual(self.decision_audits(decision), [])
                    self.assertIs(outcome["audit_recorded"], self.audit_bytes() != before)
                    self.assertEqual(self.state_of(target), ("unacknowledged", True))
                    if branch == "lost":
                        self.lose_replacement(state)
                    self.restart_and_converge()
                    if branch == "kept":
                        decision = self.decision_for(target)
                        self.assertEqual(self.completions()[decision["sequence"]], "recorded")
                        self.assertEqual(len(self.decision_audits(decision)), 1)
                        self.assertEqual(self.state_of(target), ("acknowledged", False))
                    else:
                        self.assertIsNone(self.decision_for(target))
                        self.assertEqual(self.state_of(target), ("unacknowledged", False))

    async def test_recorded_completion_failing_after_replacement(self):
        for stage in AFTER_REPLACEMENT:
            for branch in ("kept", "lost"):
                with self.subTest(stage=stage, branch=branch), no_home_assistant(self):
                    target = next(self.targets)
                    with self.failing_write(2, stage) as state:
                        outcome = self.acknowledge("change_plan", target)
                    decision = self.decision_for(target)
                    # The decision is audited: whichever completion survives,
                    # it converges to effective, so it is never refused.
                    self.assert_pending(outcome, decision)
                    self.assertIs(outcome["audit_recorded"], True)
                    self.assertEqual(len(self.decision_audits(decision)), 1)
                    if branch == "lost":
                        self.lose_replacement(state)
                        self.assertNotIn(decision["sequence"], self.completions())
                    self.restart_and_converge()
                    self.assertEqual(self.completions()[decision["sequence"]], "recorded")
                    self.assertEqual(len(self.decision_audits(decision)), 1)
                    self.assertEqual(self.state_of(target), ("acknowledged", False))

    async def test_abandoned_completion_failing_after_replacement(self):
        for stage in AFTER_REPLACEMENT:
            for branch in ("kept", "lost"):
                with self.subTest(stage=stage, branch=branch), no_home_assistant(self):
                    target, before = next(self.targets), self.audit_bytes()
                    with self.failing_write(2, stage) as state, patch.object(
                        self.service.audit, "write_batch", return_value=0
                    ):
                        outcome = self.acknowledge("change_plan", target)
                    decision = self.decision_for(target)
                    # An `abandoned` completion that may vanish cannot be
                    # reported as a settled refusal: the decision could
                    # still be reconciled and take effect.
                    self.assert_pending(outcome, decision)
                    self.assertIs(outcome["audit_recorded"], False)
                    self.assertEqual(self.audit_bytes(), before)
                    if branch == "lost":
                        self.lose_replacement(state)
                    self.restart_and_converge()
                    if branch == "kept":
                        self.assertEqual(self.completions()[decision["sequence"]], "abandoned")
                        self.assertEqual(self.decision_audits(decision), [])
                        self.assertEqual(self.state_of(target), ("unacknowledged", False))
                    else:
                        self.assertEqual(self.completions()[decision["sequence"]], "recorded")
                        self.assertEqual(len(self.decision_audits(decision)), 1)
                        self.assertEqual(self.state_of(target), ("acknowledged", False))

    async def test_failures_before_replacement_are_settled(self):
        # Controls: before os.replace nothing new is visible, so a failed
        # decision is a settled refusal and a failed completion is pending.
        with no_home_assistant(self):
            target = next(self.targets)
            with self.failing_write(1, "before_replace"):
                outcome = self.acknowledge("change_plan", target)
            self.assertEqual(outcome["status"], "refused")
            self.assertEqual(outcome["refusal_reason"], "acknowledgement_persistence_failed")
            self.assertIsNone(outcome["ledger_sequence"])
            self.assertFalse(self.ledger_path().exists())
            self.restart()
            self.assertFalse(self.ledger_path().exists())
            self.assertEqual(self.state_of(target), ("unacknowledged", False))

            with self.failing_write(2, "before_replace"):
                outcome = self.acknowledge("change_plan", target)
            decision = self.decision_for(target)
            self.assert_pending(outcome, decision)
            self.assertEqual(self.completions(), {})
            self.restart_and_converge()
            self.assertEqual(self.completions(), {decision["sequence"]: "recorded"})
            self.assertEqual(self.state_of(target), ("acknowledged", False))


if __name__ == "__main__":
    unittest.main()
