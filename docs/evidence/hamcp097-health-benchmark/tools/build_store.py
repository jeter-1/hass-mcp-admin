#!/usr/bin/env python
"""HAMCP-097 offline benchmark fixture: synthetic persisted governance store.

Builds a deterministic, all-terminal governance store at "live scale" x
--scale through the REAL writers: ChangeGovernanceService plan creation,
the Ingress approval path (approve -> issue_external_csrf ->
decide_external_approval), and apply through the composed F3 runtime
(F3RuntimeIntegration) with the repository's offline fake gateways.

Synthetic values only; no network; no live Home Assistant.

Layout of --out DIR:
  DIR/governance/   the governance_path (plans, tasks, F3 namespaces)
  DIR/audit.jsonl   the audit log (production keeps it outside governance_path)
  DIR/build_summary.json
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import secrets
import shutil
import sys
import time
import uuid

def _source_root() -> Path:
    # --source-root (dir containing hass_mcp_engineering_beta); default is the
    # frozen base clone so later worktree edits cannot change the fixture.
    for i, arg in enumerate(sys.argv):
        if arg == "--source-root" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]).resolve()
        if arg.startswith("--source-root="):
            return Path(arg.split("=", 1)[1]).resolve()
    return Path(__file__).resolve().parent / "base"


WORKTREE = _source_root()
sys.path[:0] = [
    str(WORKTREE),
    str(WORKTREE / "hass_mcp_engineering_beta"),
    str(WORKTREE / "tests"),
]

# ---------------------------------------------------------------- determinism
_RNG = random.Random(0x097)


def _det_uuid4():
    return uuid.UUID(int=_RNG.getrandbits(128), version=4)


def _det_token_bytes(n=32):
    return bytes(_RNG.getrandbits(8) for _ in range(n or 32))


def _det_token_hex(n=32):
    return _det_token_bytes(n).hex()


def _det_token_urlsafe(n=32):
    import base64

    return base64.urlsafe_b64encode(_det_token_bytes(n)).rstrip(b"=").decode()


uuid.uuid4 = _det_uuid4
secrets.token_bytes = _det_token_bytes
secrets.token_hex = _det_token_hex
secrets.token_urlsafe = _det_token_urlsafe

import logging  # noqa: E402

logging.disable(logging.INFO)
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
    is_terminal_plan,
)
from ha_mcp_engineering.governance.task_models import TERMINAL_TASK_STATES  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from tests.test_dev14_configuration_plans import FakeConfigurationGateway  # noqa: E402
from tests.test_f3_runtime_integration import (  # noqa: E402
    _ExactFakeConfigurationGateway,
    _F3LifecycleGateway,
    _provider_identity,
)
from tests.test_2_1a_operational_backup import FakeOperationalGateway  # noqa: E402
from tests.test_readonly_upstream_gateway import settings as synthetic_settings  # noqa: E402

SETTINGS = synthetic_settings()
SENSITIVE = (SETTINGS.access_secret, SETTINGS.ha_token)
APPROVER = "home_assistant_admin_ingress:h097-synthetic-admin"
BASE_TIME = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)

# Plan mix at scale 1 (501 plans). The live mix is unknown; this is a
# representative choice. Keys are build "kinds".
MIX_X1 = {
    "config_applied": 333,       # automation/script/helper, verified via F3
    "config_rejected": 30,       # Ingress reject decision
    "config_expired": 32,        # never approved; clock passes expiry
    "config_apply_failed": 12,   # provider write failure after dispatch
    "config_prohibited": 14,     # policy class prohibited at creation
    "config_rollback": 8,        # applied, then F3 rollback plan applied (2 plans)
    "backup_applied": 20,
    "reload_applied": 30,
    "addon_restart_applied": 6,
    "ha_restart_applied": 6,
    "ops_rejected": 1,
    "manual_review": 1,          # reload, response lost -> manual review
}
# config_rollback produces 2 plans each: 333+30+32+12+14+16+20+30+6+6+1+1 = 501
# Tasks: every applied/failed/rollback/operational plan + manual review = 424.

PROJECTION_FAILURES = 2  # tampered in place after the build, scale-independent

AUTOMATION_POOL = 140
SCRIPT_POOL = 40


class Clock:
    def __init__(self):
        self.value = BASE_TIME

    def __call__(self):
        return self.value

    def advance(self, **values):
        self.value += timedelta(**values)


# ----------------------------------------------------------- config content
WORDS = (
    "living room kitchen hallway porch garage office bedroom upstairs "
    "downstairs sunrise sunset motion presence humidity temperature "
    "evening morning weekday weekend vacation guest comfort eco quiet "
    "reminder battery window door ventilation laundry dishwasher"
).split()


def _sentence(rng, n):
    return " ".join(rng.choice(WORDS) for _ in range(n)).capitalize()


def _trigger(rng, i):
    kind = rng.choice(("state", "time", "numeric_state", "sun"))
    if kind == "state":
        return {
            "platform": "state",
            "entity_id": f"binary_sensor.{rng.choice(WORDS)}_motion_{i}",
            "to": "on",
            "for": {"seconds": rng.randint(0, 120)},
        }
    if kind == "time":
        return {"platform": "time", "at": f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00"}
    if kind == "numeric_state":
        return {
            "platform": "numeric_state",
            "entity_id": f"sensor.{rng.choice(WORDS)}_temperature_{i}",
            "above": rng.randint(15, 30),
        }
    return {"platform": "sun", "event": rng.choice(("sunrise", "sunset")),
            "offset": f"-00:{rng.randint(0, 59):02d}:00"}


def _condition(rng, i):
    return {
        "condition": "state",
        "entity_id": f"input_boolean.{rng.choice(WORDS)}_mode_{i}",
        "state": rng.choice(("on", "off")),
    }


def _action(rng, i, policy):
    if policy == "elevated":
        return {
            "service": "light.turn_on",
            "target": {"entity_id": f"light.{rng.choice(WORDS)}_{i}"},
            "data": {"brightness_pct": rng.randint(10, 100), "transition": 2},
        }
    if policy == "prohibited":
        return {"service": "lock.unlock", "target": {"entity_id": f"lock.{rng.choice(WORDS)}_{i}"}}
    # No script.* calls: an unresolved callee is scanned as a possible
    # unlock/disarm (physical_effects._unlock_or_disarm_scan) -> prohibited.
    choice = rng.choice(("notify", "delay", "helper"))
    if choice == "notify":
        return {
            "service": "notify.notify",
            "data": {"title": _sentence(rng, 4), "message": _sentence(rng, rng.randint(12, 60))},
        }
    if choice == "delay":
        return {"delay": {"seconds": rng.randint(1, 30)}}
    return {
        "service": "input_boolean.turn_on",
        "target": {"entity_id": f"input_boolean.{rng.choice(WORDS)}_flag_{i}"},
    }


def automation_config(rng, target_id, policy, size):
    cfg = {
        "alias": f"H097 {_sentence(rng, 3)} {target_id}",
        "description": _sentence(rng, rng.randint(20, 80)),
        "mode": rng.choice(("single", "restart", "queued")),
        "trigger": [_trigger(rng, i) for i in range(max(1, size // 4))],
        "condition": [_condition(rng, i) for i in range(max(0, size // 6))],
        "action": [_action(rng, i, policy) for i in range(max(1, size))],
    }
    if policy == "prohibited":
        # One lock.unlock classifies elevated (safety-critical ack). More than
        # MAX_ACKNOWLEDGED_OBJECTS (32) protected objects exceeds the review
        # bound and classifies prohibited (observed, policy f2-v3).
        cfg["action"].append({"service": "lock.unlock", "target": {
            "entity_id": [f"lock.h097_door_{n}" for n in range(34)]}})
    if policy == "elevated":
        cfg["action"].append(_action(rng, 998, "elevated"))
    return cfg


def script_config(rng, target_id, size):
    return {
        "alias": f"H097 script {_sentence(rng, 3)} {target_id}",
        "description": _sentence(rng, rng.randint(10, 50)),
        "mode": "single",
        "sequence": [_action(rng, i, "standard") for i in range(max(1, size))],
    }


def sized(rng):
    # Mostly a few KB; a tail of large automations (tens of KB on disk with
    # pre-image + proposal + projections).
    roll = rng.random()
    if roll < 0.70:
        return rng.randint(1, 4)
    if roll < 0.95:
        return rng.randint(4, 10)
    return rng.randint(10, 24)


class SyntheticConfigurationGateway(FakeConfigurationGateway):
    """dev14 fake with a seeded pool of existing automations and scripts."""

    def __init__(self, rng):
        super().__init__()
        for index in range(AUTOMATION_POOL):
            target = f"h097_automation_{index}"
            config = automation_config(rng, target, "standard", sized(rng))
            config["id"] = target
            self.configs[("automation", target)] = config
        for index in range(SCRIPT_POOL):
            target = f"h097_script_{index}"
            config = script_config(rng, target, sized(rng))
            config["id"] = target
            self.configs[("script", target)] = config

    async def get(self, automation_id):
        # Legacy automation interface (create_plan path), as
        # tests/test_governance.FakeGateway.get.
        config = self.configs.get(("automation", automation_id))
        return copy.deepcopy(config) if config is not None else None


# ------------------------------------------------------------------ builder
class Builder:
    def __init__(self, out: Path, scale: int):
        self.out = out
        self.scale = scale
        self.divisor = 1
        self.governance = out / "governance"
        self.rng = random.Random(9700 + scale)
        self.clock = Clock()
        self.gateway = SyntheticConfigurationGateway(random.Random(97))
        self.lifecycle = _F3LifecycleGateway()
        self.lifecycle.now = self.clock
        self.operational = FakeOperationalGateway()
        self.repository = ChangePlanRepository(self.governance)
        self.service = ChangeGovernanceService(
            self.repository,
            self.gateway,
            AuditLogger(str(out / "audit.jsonl"), SETTINGS.access_secret),
            now=self.clock,
            sensitive_values=SENSITIVE,
            operational_gateway=self.operational,
            lifecycle_gateway=self.lifecycle,
        )
        self.runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(self.governance),
            configuration_gateway=_ExactFakeConfigurationGateway(self.gateway),
            backup_gateway=self.operational,
            lifecycle_gateway=self.lifecycle,
            provider_identity_reader=_provider_identity,
            retention_days=SETTINGS.governance_retention_days,
        )
        self.service.f3_runtime = self.runtime
        self.helper_seq = 0
        self.kind_counts: dict[str, int] = {}
        self.plan_kinds: dict[str, str] = {}
        self.outcomes: dict[str, int] = {}

    # -- helpers ----------------------------------------------------------
    def tick(self, minutes=7):
        self.clock.advance(minutes=minutes)

    def note(self, kind, plan_id):
        self.kind_counts[kind] = self.kind_counts.get(kind, 0) + 1
        self.plan_kinds[plan_id] = kind

    async def approve(self, plan_id, plan_hash, *, reject=False):
        pending = self.service.approve(plan_id, plan_hash)
        review, csrf = await self.service.issue_external_csrf(plan_id, pending["challenge_id"])
        granted = await self.service.decide_external_approval(
            plan_id=plan_id, challenge_id=pending["challenge_id"],
            expected_plan_hash=plan_hash, approval_kind=pending["approval_kind"],
            approval_action=pending["approval_action"], csrf_nonce=csrf,
            decision="reject" if reject else "approve", approver_principal=APPROVER,
        )
        if not reject and granted.get("status") == "approval_pending":
            second_review, second_csrf = await self.service.issue_external_csrf(
                plan_id, granted["challenge_id"])
            safety = second_review.get("safety_critical_acknowledgement") or {}
            granted = await self.service.decide_external_approval(
                plan_id=plan_id, challenge_id=granted["challenge_id"],
                expected_plan_hash=plan_hash, approval_kind=granted["approval_kind"],
                approval_action=granted["approval_action"], csrf_nonce=second_csrf,
                decision="approve", approver_principal=APPROVER,
                acknowledged_objects=tuple(r["object_ref"] for r in safety.get("objects", [])),
                acknowledged_projection_hash=safety.get("projection_hash", ""),
            )
        return granted

    def config_operations(self, policy, *, update_only=False):
        rng = self.rng
        roll = rng.random()
        if update_only:
            # F3 rollback reverses update children only; a create-only plan
            # has no reversible child (ROLLBACK_NOT_AVAILABLE).
            roll = roll * 0.82
        if policy == "prohibited":
            self.helper_seq += 1
            target = f"h097_new_automation_{self.helper_seq}"
            return [{
                "operation_id": "create_automation", "resource_type": "automation",
                "action": "create", "target_id": target, "depends_on": [],
                "proposed_config": automation_config(rng, target, "prohibited", sized(rng)),
            }]
        if roll < 0.62:
            target = f"h097_automation_{rng.randrange(AUTOMATION_POOL)}"
            return [{
                "operation_id": "update_automation", "resource_type": "automation",
                "action": "update", "target_id": target, "depends_on": [],
                "proposed_config": automation_config(rng, target, policy, sized(rng)),
            }]
        if roll < 0.82:
            target = f"h097_script_{rng.randrange(SCRIPT_POOL)}"
            return [{
                "operation_id": "update_script", "resource_type": "script",
                "action": "update", "target_id": target, "depends_on": [],
                "proposed_config": script_config(rng, target, sized(rng)),
            }]
        # helper create, optionally followed by an automation update using it
        self.helper_seq += 1
        helper_type = rng.choice(("input_boolean", "input_number"))
        helper_id = f"{helper_type}.h097_helper_{self.helper_seq}"
        proposed = (
            {"name": f"H097 helper {self.helper_seq}", "icon": "mdi:toggle-switch"}
            if helper_type == "input_boolean"
            else {"name": f"H097 helper {self.helper_seq}", "min": 0, "max": 100,
                  "step": 1, "mode": "slider", "unit_of_measurement": "%"}
        )
        ops = [{
            "operation_id": "create_helper", "resource_type": "helper",
            "helper_type": helper_type, "action": "create", "target_id": helper_id,
            "depends_on": [], "proposed_config": proposed,
        }]
        if rng.random() < 0.5:
            target = f"h097_automation_{rng.randrange(AUTOMATION_POOL)}"
            ops.append({
                "operation_id": "update_automation", "resource_type": "automation",
                "action": "update", "target_id": target, "depends_on": ["create_helper"],
                "proposed_config": automation_config(rng, target, policy, sized(rng)),
            })
        return ops

    async def create_config(self, policy, *, expiration_minutes=120, update_only=False):
        return await self.service.create_configuration_plan(
            title=f"H097 synthetic {_sentence(self.rng, 4)}",
            description=_sentence(self.rng, self.rng.randint(8, 30)),
            operations=self.config_operations(policy, update_only=update_only),
            expiration_minutes=expiration_minutes,
            caller_context={"benchmark": "hamcp-097"},
        )

    def record_outcome(self, result):
        state = result.get("task_state", result.get("state", result.get("status")))
        self.outcomes[str(state)] = self.outcomes.get(str(state), 0) + 1
        return state

    # -- kinds ------------------------------------------------------------
    async def k_config_applied(self):
        policy = "elevated" if self.rng.random() < 0.3 else "standard"
        created = await self.create_config(policy)
        self.note("config_applied", created["plan_id"])
        await self.approve(created["plan_id"], created["plan_hash"])
        self.record_outcome(await self.service.apply(created["plan_id"], created["plan_hash"]))

    async def k_config_rejected(self):
        policy = "elevated" if self.rng.random() < 0.3 else "standard"
        created = await self.create_config(policy)
        self.note("config_rejected", created["plan_id"])
        await self.approve(created["plan_id"], created["plan_hash"], reject=True)

    async def k_config_expired(self):
        created = await self.create_config("standard", expiration_minutes=5)
        self.note("config_expired", created["plan_id"])
        # the next tick (7 min) passes the 5-minute expiry

    async def k_config_apply_failed(self):
        # Single-child update: a failed child after an earlier child was
        # written ends in manual_review_required (partial apply), which would
        # break the "exactly one manual-review task" shape.
        created = await self.create_config("standard", update_only=True)
        self.note("config_apply_failed", created["plan_id"])
        await self.approve(created["plan_id"], created["plan_hash"])
        plan = self.service._load(created["plan_id"])
        last = plan.operations[-1]
        rtype = last.helper_type if last.resource_type == "helper" else last.resource_type
        self.gateway.fail_write_target = (rtype, last.target_id)
        try:
            self.record_outcome(await self.service.apply(created["plan_id"], created["plan_hash"]))
        finally:
            self.gateway.fail_write_target = None

    async def k_config_prohibited(self):
        # Same writer path as test_f2_policy_approval.create_prohibited
        # (create_plan update_automation with lock.unlock). A
        # create_configuration_plan variant classified prohibited (>32
        # protected objects) was observed to persist risk.apply_allowed=True
        # and health then counts it as a projection failure
        # (approval_sequence_inconsistent), so it is not used here.
        target = f"h097_automation_{self.rng.randrange(AUTOMATION_POOL)}"
        proposed = copy.deepcopy(self.gateway.configs[("automation", target)])
        proposed["action"] = list(proposed["action"]) + [
            {"service": "lock.unlock", "target": {"entity_id": f"lock.h097_door_{self.rng.randrange(8)}"}}]
        created = await self.service.create_plan(
            title=f"H097 synthetic prohibited {_sentence(self.rng, 3)}",
            description=_sentence(self.rng, 12), operation="update_automation",
            automation_id=target, proposed_config=proposed)
        self.note("config_prohibited", created["plan_id"])

    async def k_config_rollback(self):
        created = await self.create_config("standard", update_only=True)
        self.note("config_rollback_source", created["plan_id"])
        await self.approve(created["plan_id"], created["plan_hash"])
        self.record_outcome(await self.service.apply(created["plan_id"], created["plan_hash"]))
        self.tick(3)
        source = self.service._load(created["plan_id"])
        rollback = await self.service.rollback_change(source.plan_id, self.service.plan_hash(source))
        reverse_id = rollback["rollback_plan_id"]
        self.note("config_rollback_inverse", reverse_id)
        reverse = self.service.get_plan(reverse_id)
        reverse_hash = reverse.get("plan_hash") or self.service.plan_hash(self.service._load(reverse_id))
        await self.approve(reverse_id, reverse_hash)
        self.record_outcome(await self.service.apply(reverse_id, reverse_hash))

    async def _apply_op(self, kind, created, *, ha_restart=False):
        plan = created["plan"]
        self.note(kind, plan["plan_id"])
        await self.approve(plan["plan_id"], plan["plan_hash"])
        result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        if ha_restart:
            for _ in range(4):
                if result.get("task_state", result.get("state")) == "succeeded_verified":
                    break
                self.clock.advance(seconds=120)
                await self.runtime.recover_once("h097_build")
                result = self.service.get_execution_task(result["task_id"])
        self.record_outcome(result)

    async def k_backup_applied(self):
        await self._apply_op("backup_applied", await self.service.create_backup_plan(
            backup_name=f"H097 synthetic {self.kind_counts.get('backup_applied', 0)}"))

    async def k_reload_applied(self):
        target = self.rng.choice(("automation", "script", "input_boolean", "input_number"))
        await self._apply_op("reload_applied", await self.service.create_reload_plan(reload_target=target))

    async def k_addon_restart_applied(self):
        await self._apply_op("addon_restart_applied",
                             await self.service.create_addon_restart_plan(addon_slug="local_test_addon"))

    async def k_ha_restart_applied(self):
        await self._apply_op("ha_restart_applied",
                             await self.service.create_home_assistant_restart_plan(), ha_restart=True)

    async def k_ops_rejected(self):
        created = await self.service.create_reload_plan(reload_target="automation")
        plan = created["plan"]
        self.note("ops_rejected", plan["plan_id"])
        await self.approve(plan["plan_id"], plan["plan_hash"], reject=True)

    async def k_manual_review(self):
        # test_f3_runtime_integration::test_operational_response_loss_uses_
        # observation_without_redispatch: provider response lost after
        # dispatch, evidence window passes, recovery terminalizes the task in
        # manual_review_required (retaining a selective conflict hold).
        self.lifecycle.mode = "ambiguous"
        created = await self.service.create_reload_plan(reload_target="automation")
        plan = created["plan"]
        self.note("manual_review", plan["plan_id"])
        await self.approve(plan["plan_id"], plan["plan_hash"])
        try:
            result = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        finally:
            self.lifecycle.mode = "success"
        self.clock.advance(seconds=901)
        await self.runtime.recover_once("h097_manual_review_sweep")
        result = self.service.get_execution_task(result["task_id"])
        self.record_outcome(result)
        self.manual_review_plan_id = plan["plan_id"]

    # -- drive ------------------------------------------------------------
    async def build(self):
        telemetry, context = begin_request("h097-build-store")
        telemetry.caller_id = "h097-synthetic-caller"
        try:
            await self.runtime.recover_once("startup")
            schedule = []
            for kind, count in MIX_X1.items():
                if kind == "manual_review":
                    continue
                schedule.extend([kind] * max(1, (count * self.scale) // self.divisor))
            self.rng.shuffle(schedule)
            started = time.monotonic()
            for index, kind in enumerate(schedule):
                await getattr(self, f"k_{kind}")()
                self.tick(7)
                if index % 50 == 0:
                    print(f"[build x{self.scale}] {index}/{len(schedule)} "
                          f"{time.monotonic() - started:.1f}s", file=sys.stderr, flush=True)
            # Exactly one manual-review task, created last so its retained
            # conflict hold cannot block any other build step.
            await self.k_manual_review()
            self.tick(7)
            # Persist expiry and settle every lifecycle through the real
            # owner path (health resolves and persists expiry), then one
            # recovery pass, as a running server would.
            await self.service.async_health_summary(
                home_assistant_status="not_checked", home_assistant_websocket_status="not_checked")
            await self.runtime.recover_once("h097_settle")
            self.tamper_projection_failures()
        finally:
            end_request(context)

    def tamper_projection_failures(self):
        """Two in-place record edits that load but fail projection.

        (1) HAMCP-198 corrupt_subject: policy_subject_hash := 0*64 on a
            rejected plan -> POLICY_SNAPSHOT_MISMATCH.
        (2) approval-bundle field: approval.policy_decision_hash := 2*64 on an
            applied plan (HAMCP-198 approval_policy_binding_mismatch case)
            -> POLICY_SNAPSHOT_MISMATCH.
        Records are rewritten with the same canonical JSON the repository uses
        so the safety detector and loader still accept them.
        """
        by_kind: dict[str, list[str]] = {}
        for plan_id, kind in sorted(self.plan_kinds.items()):
            by_kind.setdefault(kind, []).append(plan_id)
        targets = [
            (by_kind["config_rejected"][0], "subject"),
            (by_kind["config_applied"][0], "approval_binding"),
        ][:PROJECTION_FAILURES]
        self.tampered = []
        for plan_id, how in targets:
            plan = self.repository.get(plan_id)
            path = self.repository._path_for_plan(plan)
            data = json.loads(path.read_text(encoding="utf-8"))
            if how == "subject":
                data["policy_decision"]["policy_subject_hash"] = "0" * 64
            else:
                data["approval"]["policy_decision_hash"] = "2" * 64
            path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            self.tampered.append({"plan_id": plan_id, "kind": self.plan_kinds[plan_id], "field": how})


def summarize(out: Path, builder: Builder | None = None) -> dict:
    governance = out / "governance"
    repository = ChangePlanRepository(governance)
    service = ChangeGovernanceService(
        repository, FakeConfigurationGateway(), None,
        now=lambda: BASE_TIME + timedelta(days=30), sensitive_values=SENSITIVE,
    )
    plans = repository.list()
    policy = {}
    status = {}
    operation = {}
    nonterminal = 0
    for plan in plans:
        cls = plan.policy_decision.policy_class.value if plan.policy_decision else "legacy_without_policy_snapshot"
        policy[cls] = policy.get(cls, 0) + 1
        status[plan.status.value] = status.get(plan.status.value, 0) + 1
        operation[plan.operation.value] = operation.get(plan.operation.value, 0) + 1
        nonterminal += 0 if is_terminal_plan(plan) else 1
    tasks = service.task_repository.list()
    states = {}
    events = 0
    for task in tasks:
        states[task.state.value] = states.get(task.state.value, 0) + 1
        events += len(task.events)
    health = service.health_summary(home_assistant_status="not_checked",
                                    home_assistant_websocket_status="not_checked")
    total_bytes = 0
    file_count = 0
    plan_sizes = []
    for path in out.rglob("*"):
        if path.is_file():
            size = path.stat().st_size
            total_bytes += size
            file_count += 1
    for plan in plans:
        plan_sizes.append(repository._path_for_plan(plan).stat().st_size)
    plan_sizes.sort()
    summary = {
        "plan_count": len(plans),
        "nonterminal_plan_count": nonterminal,
        "policy_class_counts": dict(sorted(policy.items())),
        "plan_status_counts": dict(sorted(status.items())),
        "plan_operation_counts": dict(sorted(operation.items())),
        "health_plans_by_policy_class": health.get("plans_by_policy_class"),
        "projection_failure_count": health.get("projection_failure_count"),
        "projection_failures": health.get("projection_failures"),
        "task_count": len(tasks),
        "event_count": events,
        "task_state_counts": dict(sorted(states.items())),
        "tasks_manual_review": states.get("manual_review_required", 0),
        "nonterminal_task_count": sum(
            1 for t in tasks if t.state not in TERMINAL_TASK_STATES),
        "plan_file_bytes": {
            "min": plan_sizes[0] if plan_sizes else 0,
            "median": plan_sizes[len(plan_sizes) // 2] if plan_sizes else 0,
            "p90": plan_sizes[int(len(plan_sizes) * 0.9)] if plan_sizes else 0,
            "max": plan_sizes[-1] if plan_sizes else 0,
            "total": sum(plan_sizes),
        },
        "file_count": file_count,
        "total_bytes_on_disk": total_bytes,
    }
    if builder is not None:
        summary["scale"] = builder.scale
        summary["build_kind_counts"] = dict(sorted(builder.kind_counts.items()))
        summary["build_apply_outcomes"] = dict(sorted(builder.outcomes.items()))
        summary["tampered"] = builder.tampered
        summary["fake_clock_end"] = builder.clock().isoformat()
    return summary


async def main_async(args):
    out = Path(args.out).resolve()
    if out.exists():
        if not args.force:
            raise SystemExit(f"{out} exists (use --force)")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    started = time.monotonic()
    builder = Builder(out, args.scale)
    builder.divisor = args.probe_divisor
    await builder.build()
    summary = summarize(out, builder)
    summary["build_seconds"] = round(time.monotonic() - started, 1)
    (out / "build_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(json.dumps(summary, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=int, choices=(1, 2, 4), default=1)
    parser.add_argument("--out", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--probe-divisor", type=int, default=1)
    parser.add_argument("--source-root", default=None)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    if args.summarize_only:
        print(json.dumps(summarize(Path(args.out).resolve()), sort_keys=True))
        return
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
