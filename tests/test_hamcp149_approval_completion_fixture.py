"""HAMCP-149 Layer A: offline approval-completion compatibility fixture.

Implementation default (pending Josh, design note Q2): signal S0. The
authoritative completion check is the existing persisted-only
``get_change_plan`` read. The server gains no completion field, no
server-to-client notification, no push and no persistence. This module is the
non-executing compatibility fixture of the design note, section 3.1, Layer A:

* a test-local reference client recipe (``classify_completion`` and
  ``CompletionFixtureMixin.bounded_recheck``) that maps the existing summary
  fields to a completion outcome, binds it to the exact plan id and plan hash
  the requesting conversation holds, stops within a fixed active-turn budget
  and fails closed on anything it does not recognize;
* governance scenarios driven through the real service and the persisted
  Ingress pane decision: standard, elevated (two stages), safety-critical
  (two stages), rejection at each stage, challenge expiry (projected, then
  persisted on touch), plan expiry, replan and plan-hash mismatch, stale
  state, another caller, and storage failure;
* loopback Streamable HTTP rows with the pinned SDK client, stateless and
  stateful, that record what the server negotiates and prove that a
  notification unrelated to a request is never delivered without a GET stream
  and that a mid-call disconnect leaves the plan unchanged.

A completion observation is never execution authority. A test continues only
through ``apply`` with the exact plan hash, which re-checks approval validity,
stale state and at-most-once dispatch on its own. The recipe is not runtime
code and selects no S1 or S2 output schema.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import Context
import uvicorn
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import GOVERNANCE  # noqa: E402
from ha_mcp_engineering.governance.models import (  # noqa: E402
    ApprovalActionKind,
    ApprovalState,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    SECOND_ACTION_PENDING_STATES,
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
    ChangePlanStorageError,
)
from ha_mcp_engineering.mcp_sdk_compatibility import (  # noqa: E402
    install_catalog_generation_gate,
)
from ha_mcp_engineering.mcp_server import create_mcp_server  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools.governance import get_change_plan  # noqa: E402
from tests.test_beta33_risk_delta import (  # noqa: E402
    CURRENT_GARAGE_AUTOMATION,
    GARAGE_TARGET,
)
from tests.test_dev14_configuration_plans import (  # noqa: E402
    ConfigurationPlanTestCase,
)
from tests.test_f2_policy_approval import (  # noqa: E402
    ADMIN_A,
    F2ApprovalTestCase,
)


# --- Reference client recipe (test-local; not runtime) ----------------------

SUMMARY_MODEL = "change-plan-observability-v1"
SUMMARY_FIELDS = (
    "status",
    "plan_hash",
    "approval_state",
    "approval_lifecycle",
    "approval_bundle_state",
    "approval_actionable",
    "apply_allowed",
    "next_required_operation",
)
# Active-turn budget: a fixed number of authoritative re-reads, spaced by a
# fixed interval. Both are well under the inbound rate limit.
MAX_ACTIVE_TURN_CHECKS = 6
RECHECK_AFTER_SECONDS = 10

SECOND_ACTION_LIFECYCLES = frozenset(SECOND_ACTION_PENDING_STATES.values())
# Every lifecycle value ``_approval_lifecycle`` can produce today, mapped to the
# recipe outcome. ``approval_invalidated`` is split by plan status below.
LIFECYCLE_OUTCOMES = {
    "approval_not_requested": "approval_not_requested",
    "approval_pending_external": "awaiting_decision",
    "pending_elevated_risk_acknowledgement": "awaiting_second_action",
    "pending_safety_critical_acknowledgement": "awaiting_second_action",
    "approved": "approved_apply_ready",
    "approval_rejected": "rejected",
    "approval_expired": "expired",
    "approval_invalidated": "invalidated",
    "approval_consumed": "consumed",
    "prohibited": "prohibited",
}
CONTINUE_CHECKING = frozenset({"awaiting_decision", "awaiting_second_action"})


def classify_completion(summary, *, plan_id: str, plan_hash: str) -> dict:
    """Map one ``get_change_plan`` summary to a completion outcome.

    ``apply_ready`` is true only for an exact, fully approved, apply-allowed
    summary of the bound plan id and hash. It is a prompt to call the guarded
    apply path, never an authorization: apply re-checks everything itself.
    """

    def outcome(state: str) -> dict:
        return {
            "state": state,
            "stop": state not in CONTINUE_CHECKING,
            "apply_ready": state == "approved_apply_ready",
        }

    if not isinstance(summary, dict) or summary.get("model") != SUMMARY_MODEL:
        return outcome("unknown")
    if summary.get("plan_id") != plan_id or summary.get("plan_hash") != plan_hash:
        return outcome("binding_mismatch")
    status = summary.get("status")
    lifecycle = summary.get("approval_lifecycle")
    apply_allowed = summary.get("apply_allowed")
    if type(apply_allowed) is not bool:
        return outcome("unknown")
    if status == "prohibited" or lifecycle == "prohibited":
        return outcome("prohibited")
    mapped = LIFECYCLE_OUTCOMES.get(lifecycle)
    if mapped is None:
        return outcome("unknown")
    if mapped == "approved_apply_ready" or apply_allowed:
        # Fail closed unless every independent fact agrees.
        if (
            mapped == "approved_apply_ready"
            and apply_allowed
            and status == "approved"
            and summary.get("approval_bundle_state") == "fully_approved"
        ):
            return outcome("approved_apply_ready")
        return outcome("unknown")
    if mapped == "invalidated":
        if status == "expired":
            return outcome("plan_expired")
        if status == "superseded":
            return outcome("replan_required")
    return outcome(mapped)


class RecordingApprovalObserver:
    """Stand-in for the advisory notification worker; records every event."""

    def __init__(self):
        self.events: list[tuple[str, str]] = []

    def observe(self, plan, event, *, request_id=None, approval_action=None):
        self.events.append((plan.plan_id, event))

    def status_for(self, challenge_id):
        return {"configured": False, "status": "disabled", "authority": "none"}

    def health_snapshot(self):
        return {"configured": False}


class CompletionFixtureMixin:
    """Shared drive helpers. Subclasses provide ``service`` and ``gateway``."""

    def install_observer(self, rebuild):
        self.observer = RecordingApprovalObserver()
        self.service = rebuild(self.observer)
        prior = GOVERNANCE.service
        GOVERNANCE.service = self.service
        self.addCleanup(setattr, GOVERNANCE, "service", prior)

    async def read_summary(self, plan_id):
        payload = json.loads(await get_change_plan(plan_id))
        if not payload.get("success"):
            return None, payload
        return payload["data"]["canonical_summary"], payload

    def advance(self):
        clock = getattr(self, "clock", None)
        if clock is not None:
            clock.advance(seconds=RECHECK_AFTER_SECONDS)

    async def bounded_recheck(self, plan_id, plan_hash, *, between=()):
        """Re-read within one active turn; never approve, decide or apply.

        ``between`` holds pane decisions that happen while the turn is active
        (Josh in the Ingress pane), one after each non-terminal read.
        """

        pending = list(between)
        observations = []
        with mock.patch.object(
            self.service,
            "apply",
            side_effect=AssertionError("recheck must never apply"),
        ):
            for _ in range(MAX_ACTIVE_TURN_CHECKS):
                summary, payload = await self.read_summary(plan_id)
                if summary is None:
                    observations.append(
                        {
                            "state": "read_failed",
                            "stop": True,
                            "apply_ready": False,
                            "error_code": payload.get("error_code"),
                            "retryable": payload.get("retryable"),
                        }
                    )
                    break
                outcome = classify_completion(
                    summary, plan_id=plan_id, plan_hash=plan_hash
                )
                outcome["fields"] = {key: summary.get(key) for key in SUMMARY_FIELDS}
                observations.append(outcome)
                if outcome["stop"]:
                    break
                if pending:
                    await pending.pop(0)()
                else:
                    self.advance()
        return observations

    @staticmethod
    def states(observations):
        return [item["state"] for item in observations]

    def plan_bytes(self, plan_id):
        files = sorted(
            path
            for path in Path(self.repository.root).rglob("*")
            if path.is_file() and plan_id in path.name
        )
        self.assertTrue(files, "plan record must be persisted")
        return {path.name: path.read_bytes() for path in files}

    def assert_no_task(self, plan_id):
        self.assertIsNone(self.service.task_repository.get_for_plan(plan_id))


# --- Governance scenarios (legacy and elevated plans) ------------------------


class CompletionScenarioTests(CompletionFixtureMixin, F2ApprovalTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.install_observer(
            lambda observer: ChangeGovernanceService(
                self.repository,
                self.gateway,
                now=self.clock,
                approval_notifications=observer,
            )
        )

    async def decide_later(self, created, pending, **kwargs):
        result = {}

        async def run():
            result.update(await self.decide(created, pending, **kwargs))

        return run, result

    async def test_standard_approval_is_recognized_and_applies_once(self):
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        before = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(before), ["approval_not_requested"])

        pending = self.service.approve(plan_id, plan_hash)
        self.assertEqual(pending["bound_plan_hash"], plan_hash)
        decision, _ = await self.decide_later(created, pending)
        seen = await self.bounded_recheck(plan_id, plan_hash, between=[decision])
        self.assertEqual(
            self.states(seen), ["awaiting_decision", "approved_apply_ready"]
        )
        self.assertTrue(seen[-1]["apply_ready"])
        self.assertTrue(seen[-1]["fields"]["apply_allowed"])
        self.assertEqual(self.gateway.writes, 0)
        self.assert_no_task(plan_id)

        # Continuation is the existing guarded apply with the exact hash.
        applied = await self.service.apply(plan_id, plan_hash)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self.gateway.writes, 1)
        after = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(after), ["consumed"])
        self.assertFalse(after[-1]["apply_ready"])
        repeated = await self.service.apply(plan_id, plan_hash)
        self.assertEqual(repeated["status"], "already_applied")
        self.assertEqual(self.gateway.writes, 1)

    async def test_elevated_first_stage_is_not_completion(self):
        created = await self.create_elevated()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        pending = self.service.approve(plan_id, plan_hash)
        stage_one = {}

        async def first():
            stage_one.update(await self.decide(created, pending))

        seen = await self.bounded_recheck(plan_id, plan_hash, between=[first])
        # The budget runs out while the second action is pending: the turn
        # ends without completion and without any apply.
        self.assertEqual(len(seen), MAX_ACTIVE_TURN_CHECKS)
        self.assertEqual(
            self.states(seen),
            ["awaiting_decision"]
            + ["awaiting_second_action"] * (MAX_ACTIVE_TURN_CHECKS - 1),
        )
        self.assertFalse(any(item["apply_ready"] for item in seen))
        self.assertEqual(
            stage_one["approval_action"],
            ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT.value,
        )
        with self.assertRaises(GovernanceError):
            await self.service.apply(plan_id, plan_hash)
        self.assertEqual(self.gateway.writes, 0)
        self.assert_no_task(plan_id)

        async def second():
            await self.decide(created, stage_one)

        seen = await self.bounded_recheck(plan_id, plan_hash, between=[second])
        self.assertEqual(
            self.states(seen), ["awaiting_second_action", "approved_apply_ready"]
        )
        applied = await self.service.apply(plan_id, plan_hash)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self.gateway.writes, 1)

    async def test_rejection_at_plan_approval_is_terminal(self):
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        pending = self.service.approve(plan_id, plan_hash)
        reject, _ = await self.decide_later(created, pending, decision="reject")
        seen = await self.bounded_recheck(plan_id, plan_hash, between=[reject])
        self.assertEqual(self.states(seen), ["awaiting_decision", "rejected"])
        self.assertTrue(seen[-1]["stop"])
        self.assertFalse(seen[-1]["apply_ready"])
        with self.assertRaises(GovernanceError):
            await self.service.apply(plan_id, plan_hash)
        self.assertEqual(self.gateway.writes, 0)
        self.assert_no_task(plan_id)

    async def test_rejection_at_second_stage_is_terminal(self):
        created = await self.create_elevated()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        pending = self.service.approve(plan_id, plan_hash)
        stage_one = await self.decide(created, pending)
        reject, _ = await self.decide_later(
            created, stage_one, decision="reject"
        )
        seen = await self.bounded_recheck(plan_id, plan_hash, between=[reject])
        self.assertEqual(
            self.states(seen), ["awaiting_second_action", "rejected"]
        )
        with self.assertRaises(GovernanceError):
            await self.service.apply(plan_id, plan_hash)
        self.assertEqual(self.gateway.writes, 0)
        self.assert_no_task(plan_id)

    async def test_challenge_expiry_is_projected_without_persistence(self):
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        pending = self.service.approve(plan_id, plan_hash)
        self.assertTrue(pending["challenge_expires_at"])
        self.clock.advance(minutes=61)
        stored_before = self.plan_bytes(plan_id)
        events_before = list(self.observer.events)

        seen = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(seen), ["expired"])
        self.assertFalse(seen[-1]["apply_ready"])
        # The read projected expiry; it persisted nothing and emitted nothing.
        self.assertEqual(self.plan_bytes(plan_id), stored_before)
        self.assertEqual(self.observer.events, events_before)
        self.assertEqual(
            self.repository.get(plan_id).approval.state,
            ApprovalState.EXTERNAL_PENDING,
        )

        # A later touch persists the expiry; the re-read is unchanged.
        self.service.pending_external_reviews()
        self.assertEqual(
            self.repository.get(plan_id).approval.state, ApprovalState.EXPIRED
        )
        self.assertIn(
            (plan_id, "external_approval_expired"), self.observer.events
        )
        seen = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(seen), ["expired"])
        with self.assertRaises(GovernanceError):
            await self.service.apply(plan_id, plan_hash)
        self.assertEqual(self.gateway.writes, 0)
        self.assert_no_task(plan_id)

        # Characterization: the summary names no next operation here, yet a
        # fresh approval request for the same hash is accepted. Recovery is a
        # new owner decision, not a continuation.
        self.assertIsNone(seen[-1]["fields"]["next_required_operation"])
        self.service.approve(plan_id, plan_hash)
        seen = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(seen)[0], "awaiting_decision")
        self.assertEqual(self.gateway.writes, 0)

    async def test_plan_expiry_after_full_approval_is_terminal(self):
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        await self.fully_approve(created)
        self.assertEqual(
            self.states(await self.bounded_recheck(plan_id, plan_hash)),
            ["approved_apply_ready"],
        )
        self.clock.advance(minutes=121)
        seen = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(seen), ["plan_expired"])
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(plan_id, plan_hash)
        self.assertEqual(raised.exception.code, ErrorCode.CHANGE_PLAN_EXPIRED)
        self.assertEqual(self.gateway.writes, 0)
        self.assert_no_task(plan_id)

    async def test_replan_supersedes_and_old_hash_never_applies(self):
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        await self.fully_approve(created)
        replacement = await self.create_standard()
        self.assertNotEqual(replacement["plan_id"], plan_id)

        seen = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(seen), ["replan_required"])
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(plan_id, plan_hash)
        self.assertEqual(
            raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_REQUIRED
        )

        # The conversation's old hash does not bind to the replacement plan.
        new_summary, _ = await self.read_summary(replacement["plan_id"])
        self.assertEqual(
            classify_completion(
                new_summary, plan_id=replacement["plan_id"], plan_hash=plan_hash
            )["state"],
            "binding_mismatch",
        )
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(replacement["plan_id"], plan_hash)
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH)
        self.assertEqual(self.gateway.writes, 0)
        self.assert_no_task(plan_id)
        self.assert_no_task(replacement["plan_id"])

    async def test_hash_mismatch_is_never_completion_and_apply_refuses(self):
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        await self.fully_approve(created)
        wrong_hash = "0" * len(plan_hash)
        seen = await self.bounded_recheck(plan_id, wrong_hash)
        self.assertEqual(self.states(seen), ["binding_mismatch"])
        self.assertFalse(seen[-1]["apply_ready"])
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(plan_id, wrong_hash)
        self.assertEqual(raised.exception.code, ErrorCode.APPROVAL_HASH_MISMATCH)
        self.assertEqual(self.gateway.writes, 0)
        # Failure preservation: the refusal consumed nothing; the exact hash
        # still recognizes completion and applies once.
        seen = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(seen), ["approved_apply_ready"])
        applied = await self.service.apply(plan_id, plan_hash)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self.gateway.writes, 1)

    async def test_stale_state_is_refused_by_apply_not_by_the_signal(self):
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        await self.fully_approve(created)
        drifted = copy.deepcopy(self.gateway.configs["fixture"])
        drifted["description"] = "Changed outside the plan"
        self.gateway.configs["fixture"] = drifted
        reads_before = self.gateway.reads

        # The persisted-only read cannot see current Home Assistant state.
        seen = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(seen), ["approved_apply_ready"])
        self.assertEqual(self.gateway.reads, reads_before)
        with self.assertRaises(GovernanceError):
            await self.service.apply(plan_id, plan_hash)
        self.assertEqual(self.gateway.writes, 0)

    async def test_other_caller_reads_the_same_truth_and_changes_nothing(self):
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        await self.fully_approve(created)
        requester_view, _ = await self.read_summary(plan_id)
        stored_before = self.plan_bytes(plan_id)
        events_before = list(self.observer.events)

        self.telemetry.caller_id = "synthetic-other-caller"
        other_view, _ = await self.read_summary(plan_id)
        self.assertEqual(other_view, requester_view)
        self.assertEqual(
            classify_completion(other_view, plan_id=plan_id, plan_hash=plan_hash)[
                "state"
            ],
            "approved_apply_ready",
        )
        self.assertEqual(self.plan_bytes(plan_id), stored_before)
        self.assertEqual(self.observer.events, events_before)
        self.assertEqual(self.repository.get(plan_id).requested_by, "mcp-requester")
        # S0 binding is the plan id and hash the requesting conversation
        # holds; the stored requester is not projected (Q3 pending Josh).
        self.assertNotIn("requested_by", other_view)
        self.assertEqual(self.gateway.writes, 0)
        self.assert_no_task(plan_id)

    async def test_characterization_apply_is_not_bound_to_requesting_caller(self):
        # Records current authority (unchanged by HAMCP-149 S0): approval binds
        # the plan id and hash, not the requesting caller. A persisted client
        # binding is design-note Q3 (pending Josh); if it lands this flips.
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        await self.fully_approve(created)
        self.telemetry.caller_id = "synthetic-other-caller"
        applied = await self.service.apply(plan_id, plan_hash)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self.gateway.writes, 1)
        self.assertEqual(self.repository.get(plan_id).requested_by, "mcp-requester")

    async def test_rechecks_are_pure_reads(self):
        created = await self.create_elevated()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        pending = self.service.approve(plan_id, plan_hash)
        await self.decide(created, pending)
        stored_before = self.plan_bytes(plan_id)
        events_before = list(self.observer.events)
        reads_before = self.gateway.reads
        with mock.patch.object(
            self.service,
            "decide_external_approval",
            side_effect=AssertionError("read must not decide"),
        ), mock.patch.object(
            self.service,
            "approve",
            side_effect=AssertionError("read must not request approval"),
        ):
            seen = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(len(seen), MAX_ACTIVE_TURN_CHECKS)
        self.assertEqual(self.plan_bytes(plan_id), stored_before)
        self.assertEqual(self.observer.events, events_before)
        self.assertEqual(self.gateway.reads, reads_before)
        self.assertEqual(self.gateway.writes, 0)
        self.assert_no_task(plan_id)

    async def test_storage_failure_surfaces_and_stops_without_apply(self):
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        await self.fully_approve(created)
        with mock.patch.object(
            self.repository,
            "get",
            side_effect=ChangePlanStorageError("synthetic storage failure"),
        ):
            seen = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(seen), ["read_failed"])
        self.assertFalse(seen[-1]["apply_ready"])
        self.assertEqual(
            seen[-1]["error_code"], ErrorCode.CHANGE_PLAN_STORAGE_ERROR.value
        )
        self.assertTrue(seen[-1]["retryable"])
        self.assertNotIn("synthetic storage failure", json.dumps(seen))
        self.assertEqual(self.gateway.writes, 0)
        # Recovery: the next authoritative re-read reports the true state.
        seen = await self.bounded_recheck(plan_id, plan_hash)
        self.assertEqual(self.states(seen), ["approved_apply_ready"])


# --- Governance scenarios (safety-critical configuration plan) ---------------


class SafetyCriticalCompletionTests(CompletionFixtureMixin, ConfigurationPlanTestCase):
    PRINCIPAL = "home_assistant_admin_ingress:hamcp149-reviewer"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gateway.configs[GARAGE_TARGET] = copy.deepcopy(
            CURRENT_GARAGE_AUTOMATION
        )
        self.install_observer(
            lambda observer: ChangeGovernanceService(
                self.repository,
                self.gateway,
                AuditLogger(str(self.audit_path), "dev14-test-access-secret"),
                now=self.service.now,
                sensitive_values=(
                    "dev14-test-access-secret",
                    "dev14-test-ha-token",
                ),
                approval_notifications=observer,
            )
        )

    async def create_safety_plan(self):
        proposed = copy.deepcopy(CURRENT_GARAGE_AUTOMATION)
        proposed.pop("id")
        proposed["action"][0]["service"] = "cover.open_cover"
        created = await self.service.create_configuration_plan(
            title="HAMCP-149 safety-critical fixture",
            description="Owner-authorized changed protected effect",
            operations=[
                {
                    "operation_id": "change_garage_effect",
                    "resource_type": "automation",
                    "action": "update",
                    "target_id": GARAGE_TARGET[1],
                    "depends_on": [],
                    "proposed_config": proposed,
                }
            ],
        )
        self.assertEqual(
            created["policy_decision"]["required_acknowledgements"],
            [
                "plan_approval",
                ApprovalActionKind.SAFETY_CRITICAL_ACKNOWLEDGEMENT.value,
            ],
        )
        return created

    async def plan_decision(self, created, pending):
        _review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], pending["challenge_id"]
        )
        return await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=pending["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"],
            csrf_nonce=csrf,
            decision="approve",
            approver_principal=self.PRINCIPAL,
        )

    async def safety_decision(self, created, stage_one, decision="approve"):
        review, csrf = await self.service.issue_external_csrf(
            created["plan_id"], stage_one["challenge_id"]
        )
        safety = review["safety_critical_acknowledgement"]
        # Object acknowledgements belong only to the approve decision; a
        # rejection that carries them is refused as a forged form.
        acknowledgement = (
            {
                "acknowledged_objects": tuple(
                    row["object_ref"] for row in safety["objects"]
                ),
                "acknowledged_projection_hash": safety["projection_hash"],
            }
            if decision == "approve"
            else {}
        )
        return await self.service.decide_external_approval(
            plan_id=created["plan_id"],
            challenge_id=stage_one["challenge_id"],
            expected_plan_hash=created["plan_hash"],
            approval_kind=stage_one["approval_kind"],
            approval_action=stage_one["approval_action"],
            csrf_nonce=csrf,
            decision=decision,
            approver_principal=self.PRINCIPAL,
            **acknowledgement,
        )

    def writes(self):
        return [call for call in self.gateway.calls if call[0] == "write"]

    async def stage_one(self, created):
        pending = self.service.approve(created["plan_id"], created["plan_hash"])
        result = {}

        async def first():
            result.update(await self.plan_decision(created, pending))

        seen = await self.bounded_recheck(
            created["plan_id"], created["plan_hash"], between=[first]
        )
        return seen, result

    async def test_plan_approval_alone_is_not_completion(self):
        created = await self.create_safety_plan()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        seen, stage_one = await self.stage_one(created)
        # No clock in this case: the budget is spent on the pending state.
        self.assertEqual(seen[0]["state"], "awaiting_decision")
        self.assertEqual(
            set(self.states(seen[1:])), {"awaiting_second_action"}
        )
        self.assertEqual(
            seen[-1]["fields"]["approval_lifecycle"],
            "pending_safety_critical_acknowledgement",
        )
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(plan_id, plan_hash)
        self.assertEqual(
            raised.exception.code,
            ErrorCode.SAFETY_CRITICAL_ACKNOWLEDGEMENT_REQUIRED,
        )
        self.assertEqual(self.writes(), [])
        self.assert_no_task(plan_id)

        async def acknowledge():
            await self.safety_decision(created, stage_one)

        seen = await self.bounded_recheck(plan_id, plan_hash, between=[acknowledge])
        self.assertEqual(
            self.states(seen), ["awaiting_second_action", "approved_apply_ready"]
        )
        applied = await self.service.apply(plan_id, plan_hash)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(
            self.states(await self.bounded_recheck(plan_id, plan_hash)),
            ["consumed"],
        )

    async def test_rejected_acknowledgement_is_terminal(self):
        created = await self.create_safety_plan()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        _seen, stage_one = await self.stage_one(created)

        async def reject():
            await self.safety_decision(created, stage_one, decision="reject")

        seen = await self.bounded_recheck(plan_id, plan_hash, between=[reject])
        self.assertEqual(self.states(seen), ["awaiting_second_action", "rejected"])
        with self.assertRaises(GovernanceError):
            await self.service.apply(plan_id, plan_hash)
        self.assertEqual(self.writes(), [])
        self.assert_no_task(plan_id)


# --- Reference recipe drift and fail-closed guards ---------------------------


class ReferenceRecipeTests(unittest.TestCase):
    PLAN_ID = "synthetic-plan"
    PLAN_HASH = "a" * 64

    def summary(self, **overrides):
        value = {
            "model": SUMMARY_MODEL,
            "plan_id": self.PLAN_ID,
            "plan_hash": self.PLAN_HASH,
            "status": "approved",
            "approval_state": "approved",
            "approval_lifecycle": "approved",
            "approval_bundle_state": "fully_approved",
            "approval_actionable": False,
            "apply_allowed": True,
            "next_required_operation": None,
        }
        value.update(overrides)
        return value

    def classify(self, summary):
        return classify_completion(
            summary, plan_id=self.PLAN_ID, plan_hash=self.PLAN_HASH
        )

    def test_recipe_covers_every_runtime_lifecycle_value(self):
        # Drift guard: a new approval state or second-action stage must be
        # classified deliberately before any client recipe relies on it.
        self.assertEqual(
            {member.value for member in ApprovalState},
            {
                "required",
                "external_pending",
                "approved",
                "rejected",
                "consumed",
                "expired",
                "invalidated",
            },
        )
        self.assertEqual(
            SECOND_ACTION_LIFECYCLES,
            {
                "pending_elevated_risk_acknowledgement",
                "pending_safety_critical_acknowledgement",
            },
        )
        self.assertTrue(SECOND_ACTION_LIFECYCLES <= set(LIFECYCLE_OUTCOMES))

    def test_only_an_exact_fully_approved_summary_is_apply_ready(self):
        self.assertEqual(self.classify(self.summary())["state"], "approved_apply_ready")
        for overrides in (
            {"status": "awaiting_approval"},
            {"approval_bundle_state": "pending_elevated_risk_acknowledgement"},
            {"apply_allowed": False},
            {"apply_allowed": "true"},
            {"apply_allowed": None},
            {"approval_lifecycle": "approval_pending_external"},
            {"approval_lifecycle": "approval_consumed"},
        ):
            with self.subTest(overrides=overrides):
                outcome = self.classify(self.summary(**overrides))
                self.assertFalse(outcome["apply_ready"])
                self.assertNotEqual(outcome["state"], "approved_apply_ready")

    def test_unrecognized_input_fails_closed(self):
        for summary in (
            None,
            [],
            "approved",
            self.summary(model="change-plan-observability-v2"),
            self.summary(approval_lifecycle="approval_resumed"),
            self.summary(approval_lifecycle=None, apply_allowed=False),
        ):
            with self.subTest(summary=summary):
                outcome = self.classify(summary)
                self.assertEqual(outcome["state"], "unknown")
                self.assertTrue(outcome["stop"])
                self.assertFalse(outcome["apply_ready"])

    def test_binding_mismatch_is_never_completion(self):
        for overrides in (
            {"plan_id": "synthetic-other-plan"},
            {"plan_hash": "b" * 64},
            {"plan_hash": None},
        ):
            with self.subTest(overrides=overrides):
                outcome = self.classify(self.summary(**overrides))
                self.assertEqual(outcome["state"], "binding_mismatch")
                self.assertFalse(outcome["apply_ready"])

    def test_prohibited_is_terminal(self):
        outcome = self.classify(
            self.summary(status="prohibited", apply_allowed=False)
        )
        self.assertEqual(outcome["state"], "prohibited")
        self.assertTrue(outcome["stop"])

    def test_budget_stays_well_under_inbound_rate_limit(self):
        options = yaml.safe_load(
            (ROOT / "hass_mcp_engineering_beta" / "config.yaml").read_text(
                encoding="utf-8"
            )
        )["options"]
        self.assertLessEqual(MAX_ACTIVE_TURN_CHECKS * 2, options["rate_limit_burst"])
        per_minute = 60 // RECHECK_AFTER_SECONDS
        self.assertLessEqual(per_minute * 10, options["rate_limit_per_minute"])


# --- Loopback Streamable HTTP rows (synthetic; no Home Assistant) ------------

SECRET = "synthetic-hamcp149-loopback-secret"
CLIENT_INFO = types.Implementation(name="synthetic-hamcp149", version="1.0.0")


def _free_loopback_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


class _LoopbackPeer:
    """A disposable loopback Engineering MCP peer over synthetic state."""

    def __init__(self, directory, *, stateful, service, clock):
        self.sock = _free_loopback_socket()
        self.port = self.sock.getsockname()[1]
        self.service = service
        self.clock = clock
        self.observed = []
        self.blocking_entered = asyncio.Event()
        self.blocking_release = asyncio.Event()
        settings = Settings(
            ha_url="http://synthetic-ha.invalid",
            ha_token="synthetic-hamcp149-token",
            access_secret=SECRET,
            port=self.port,
            audit_path=str(Path(directory) / "audit.jsonl"),
            rate_limit_per_minute=10_000,
            rate_limit_burst=1_000,
            destructive_services=frozenset(),
            ha_mcp_release_registry_enabled=stateful,
            ha_mcp_release_registry_public_key=("A" * 44) if stateful else None,
        )
        self.server = create_mcp_server(settings)
        self.server.tool(name="get_change_plan")(get_change_plan)

        @self.server.tool(name="probe_create_plan")
        async def probe_create_plan() -> str:
            proposed = {**self._fixture(), "description": "Loopback fixture"}
            created = await self.service.create_plan(
                title="HAMCP-149 loopback fixture",
                description="Synthetic loopback plan",
                operation="update_automation",
                automation_id="fixture",
                proposed_config=proposed,
            )
            return json.dumps(
                {"plan_id": created["plan_id"], "plan_hash": created["plan_hash"]}
            )

        @self.server.tool(name="probe_observe_client")
        async def probe_observe_client(ctx: Context) -> str:
            session = ctx.session
            params = session.client_params
            record = {
                "client_params_observable": params is not None,
                "client_info": (
                    params.clientInfo.model_dump() if params is not None else None
                ),
                "client_progress_token": (
                    ctx.request_context.meta.progressToken
                    if ctx.request_context.meta is not None
                    else None
                ),
                "client_has_sampling_capability": session.check_client_capability(
                    types.ClientCapabilities(sampling=types.SamplingCapability())
                ),
            }
            self.observed.append(record)
            return json.dumps(record)

        @self.server.tool(name="probe_unrelated_notification")
        async def probe_unrelated_notification(ctx: Context) -> str:
            # No related_request_id: routed to the GET stream, or dropped.
            await ctx.session.send_resource_list_changed()
            return "sent"

        @self.server.tool(name="probe_blocking_read")
        async def probe_blocking_read(plan_id: str) -> str:
            self.blocking_entered.set()
            await self.blocking_release.wait()
            return json.dumps(
                self.service.get_plan_observability(plan_id)["canonical_summary"]
            )

        if stateful:
            install_catalog_generation_gate(self.server, lambda: (1, ()))
        self.gateway = AuthenticatedMcpGateway(
            self.server.streamable_http_app(),
            settings,
            AuditLogger(settings.audit_path, SECRET),
        )
        self.uvicorn = uvicorn.Server(
            uvicorn.Config(
                self.gateway,
                lifespan="on",
                log_level="warning",
                access_log=False,
            )
        )

    @staticmethod
    def _fixture():
        from tests.test_beta25_external_approval import CURRENT

        return copy.deepcopy(CURRENT)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/{SECRET}/mcp"

    async def __aenter__(self):
        self.task = asyncio.create_task(self.uvicorn.serve(sockets=[self.sock]))
        for _ in range(200):
            if self.uvicorn.started:
                return self
            if self.task.done():
                self.task.result()
            await asyncio.sleep(0.01)
        raise AssertionError("loopback peer did not start")

    async def __aexit__(self, *exc):
        self.blocking_release.set()
        self.uvicorn.should_exit = True
        await asyncio.wait_for(self.task, timeout=10)
        self.sock.close()


class LoopbackTransportRows(CompletionFixtureMixin, F2ApprovalTestCase):
    """Layer A transport rows: what the pinned SDK negotiates on loopback."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.install_observer(
            lambda observer: ChangeGovernanceService(
                self.repository,
                self.gateway,
                now=self.clock,
                approval_notifications=observer,
            )
        )
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def peer(self, *, stateful):
        return _LoopbackPeer(
            self.directory.name,
            stateful=stateful,
            service=self.service,
            clock=self.clock,
        )

    async def client_session(self, peer, notifications):
        async def handler(message):
            if isinstance(message, types.ServerNotification):
                notifications.append(message.root.method)

        streams = streamablehttp_client(
            peer.url, timeout=5, sse_read_timeout=5, terminate_on_close=True
        )
        return streams, handler

    async def run_row(self, *, stateful):
        notifications = []
        async with asyncio.timeout(30):
            async with self.peer(stateful=stateful) as peer:
                streams, handler = await self.client_session(peer, notifications)
                async with streams as (read_stream, write_stream, session_id):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=5),
                        message_handler=handler,
                        client_info=CLIENT_INFO,
                    ) as session:
                        initialized = await session.initialize()
                        created = json.loads(
                            (await session.call_tool("probe_create_plan", {}))
                            .content[0]
                            .text
                        )
                        observed = json.loads(
                            (await session.call_tool("probe_observe_client", {}))
                            .content[0]
                            .text
                        )
                        sent = await session.call_tool(
                            "probe_unrelated_notification", {}
                        )
                        read = await session.call_tool(
                            "get_change_plan", {"plan_id": created["plan_id"]}
                        )
                        row_session_id = session_id()
        return {
            "initialized": initialized,
            "created": created,
            "observed": observed,
            "notification_sent": sent.content[0].text,
            "read": json.loads(read.content[0].text),
            "session_id": row_session_id,
            "notifications": notifications,
        }

    def assert_common_row(self, row):
        initialized = row["initialized"]
        self.assertEqual(initialized.protocolVersion, types.LATEST_PROTOCOL_VERSION)
        capabilities = initialized.capabilities
        # Nothing the server negotiates lets a client resume an ended turn.
        self.assertTrue(
            capabilities.resources is None or not capabilities.resources.subscribe
        )
        self.assertIsNone(getattr(capabilities, "tasks", None))
        self.assertIsNotNone(capabilities.tools)
        # The persisted plan read over the transport reports the true state.
        summary = row["read"]["data"]["canonical_summary"]
        self.assertEqual(
            classify_completion(
                summary,
                plan_id=row["created"]["plan_id"],
                plan_hash=row["created"]["plan_hash"],
            )["state"],
            "approval_not_requested",
        )
        # The requester is a hash of the loopback peer address, not a client
        # or conversation identity.
        stored = self.repository.get(row["created"]["plan_id"])
        self.assertEqual(
            stored.requested_by,
            hashlib.sha256(b"127.0.0.1").hexdigest()[:12],
        )
        self.assertIsNone(row["observed"]["client_progress_token"])
        self.assertFalse(row["observed"]["client_has_sampling_capability"])
        self.assertEqual(self.gateway.writes, 0)

    async def test_stateless_row(self):
        row = await self.run_row(stateful=False)
        self.assert_common_row(row)
        self.assertIsNone(row["session_id"])
        # Stateless: initialize params are not observable inside tools/call.
        self.assertFalse(row["observed"]["client_params_observable"])
        self.assertIsNone(row["observed"]["client_info"])
        # Stateless has no GET stream: the unrelated notification is dropped.
        self.assertEqual(row["notification_sent"], "sent")
        self.assertNotIn("notifications/resources/list_changed", row["notifications"])

    async def test_stateful_row_records_client_identity_as_observation_only(self):
        row = await self.run_row(stateful=True)
        self.assert_common_row(row)
        self.assertTrue(row["session_id"])
        self.assertTrue(row["observed"]["client_params_observable"])
        self.assertEqual(row["observed"]["client_info"]["name"], CLIENT_INFO.name)
        # Observation only: the stored requester still ignores clientInfo.
        stored = self.repository.get(row["created"]["plan_id"])
        self.assertNotIn(CLIENT_INFO.name, stored.requested_by)

    async def test_stateful_without_get_stream_drops_unrelated_notification(self):
        async with asyncio.timeout(30):
            async with self.peer(stateful=True) as peer:
                headers = {
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                }
                async with httpx.AsyncClient(timeout=5) as client:
                    opened = await client.post(
                        peer.url,
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": types.LATEST_PROTOCOL_VERSION,
                                "capabilities": {},
                                "clientInfo": CLIENT_INFO.model_dump(),
                            },
                        },
                    )
                    self.assertEqual(opened.status_code, 200)
                    headers["mcp-session-id"] = opened.headers["mcp-session-id"]
                    await client.post(
                        peer.url,
                        headers=headers,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    )
                    await client.post(
                        peer.url,
                        headers=headers,
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    )
                    called = await client.post(
                        peer.url,
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {
                                "name": "probe_unrelated_notification",
                                "arguments": {},
                            },
                        },
                    )
        self.assertEqual(called.status_code, 200)
        messages = [
            json.loads(line.removeprefix("data: "))
            for line in called.text.replace("\r", "").splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual([message.get("id") for message in messages], [3])
        self.assertNotIn("resources/list_changed", called.text)

    async def test_mid_call_disconnect_leaves_plan_unchanged(self):
        created = await self.create_standard()
        plan_id, plan_hash = created["plan_id"], created["plan_hash"]
        self.service.approve(plan_id, plan_hash)
        stored_before = self.plan_bytes(plan_id)
        events_before = list(self.observer.events)
        async with asyncio.timeout(30):
            async with self.peer(stateful=False) as peer:
                streams, handler = await self.client_session(peer, [])
                async with streams as (read_stream, write_stream, _session_id):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=5),
                        message_handler=handler,
                        client_info=CLIENT_INFO,
                    ) as session:
                        await session.initialize()
                        call = asyncio.create_task(
                            session.call_tool(
                                "probe_blocking_read", {"plan_id": plan_id}
                            )
                        )
                        await asyncio.wait_for(peer.blocking_entered.wait(), 5)
                        call.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await call
                # The client is gone; let the server-side read finish.
                peer.blocking_release.set()
                await asyncio.sleep(0.1)
        self.assertEqual(self.plan_bytes(plan_id), stored_before)
        self.assertEqual(self.observer.events, events_before)
        seen = await self.bounded_recheck(plan_id, plan_hash, between=[])
        self.assertEqual(seen[0]["state"], "awaiting_decision")
        self.assertEqual(self.gateway.writes, 0)
        self.assert_no_task(plan_id)


if __name__ == "__main__":
    unittest.main()
