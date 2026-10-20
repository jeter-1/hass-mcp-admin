"""HAMCP-213: the f2-v2 rollback-evidence exception stays inside F3 rollback.

f2-v3 refuses every superseded f2-v2 snapshot as current authority. The one
exception admits an exact f2-v2 snapshot as read-only source evidence when
F3 creates (and later rechecks) a governed inverse. The inverse is evaluated
under current policy. These tests use the shipped beta16 writer's records: an
applied f2-v2 source and a pending f2-v2 cached inverse.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance.models import PlanStatus  # noqa: E402
from ha_mcp_engineering.governance.policy import (  # noqa: E402
    POLICY_VERSION,
    persisted_f2_v2_policy_snapshot_matches,
    policy_snapshot_matches,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from tests import test_hamcp135_governed_rollback as rollback  # noqa: E402

PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"
SERVICE = PACKAGE / "governance" / "service.py"
PROVENANCE = rollback.HISTORICAL / "PROVENANCE.json"


def _functions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _calls(predicate):
    """Yield (module, enclosing function, call) for matching package calls."""

    for path in sorted(PACKAGE.rglob("*.py")):
        module = path.relative_to(PACKAGE).as_posix()
        seen = set()
        for function in _functions(path):
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and predicate(node) and id(node) not in seen:
                    seen.add(id(node))
                    yield module, function.name, node


def _name(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


class StructuralConfinementTests(unittest.TestCase):
    """Source-level confinement; no plan state is needed."""

    def test_rollback_source_keyword_is_passed_only_by_rollback_change(self):
        sites = [
            (module, function)
            for module, function, _node in _calls(
                lambda node: any(
                    keyword.arg == "f3_rollback_source" for keyword in node.keywords
                )
            )
        ]
        self.assertEqual(sites, [("governance/service.py", "rollback_change")])
        (_m, _f, call), = [
            row
            for row in _calls(
                lambda node: any(
                    keyword.arg == "f3_rollback_source" for keyword in node.keywords
                )
            )
        ]
        self.assertEqual(_name(call), "_load")
        value = next(
            keyword.value
            for keyword in call.keywords
            if keyword.arg == "f3_rollback_source"
        )
        # Only when F3 owns the rollback: `self.f3_runtime is not None`.
        self.assertEqual(ast.unparse(value), "self.f3_runtime is not None")

    def test_load_defaults_to_refusing_historical_snapshots(self):
        (load,) = [
            function
            for function in _functions(SERVICE)
            if function.name == "_load"
        ]
        (keyword,) = [
            (arg, default)
            for arg, default in zip(load.args.kwonlyargs, load.args.kw_defaults)
            if arg.arg == "f3_rollback_source"
        ]
        self.assertIsInstance(keyword[1], ast.Constant)
        self.assertIs(keyword[1].value, False)

    def test_f2_v2_recognizer_is_called_only_by_the_rollback_evidence_paths(self):
        sites = sorted(
            (module, function)
            for module, function, _node in _calls(
                lambda node: _name(node) == "persisted_f2_v2_policy_snapshot_matches"
            )
        )
        self.assertEqual(
            sites,
            [
                ("governance/configuration_rollback_eligibility.py", "source_snapshot"),
                ("governance/policy.py", "persisted_historical_policy_snapshot_matches"),
                ("governance/service.py", "_load"),
            ],
        )


class BehavioralConfinementTests(rollback.RollbackTests):
    """Every path except F3 rollback creation refuses the f2-v2 records."""

    async def seed_records(self):
        source = await self.seed()
        cached_id = json.loads(PROVENANCE.read_text())["cached_prohibited_inverse_id"]
        cached = self.repository.get(cached_id)
        stored_source = self.repository.get(source["plan_id"])
        for plan in (stored_source, cached):
            self.assertEqual(plan.policy_decision.policy_version, "f2-v2")
            self.assertTrue(persisted_f2_v2_policy_snapshot_matches(plan))
            self.assertFalse(policy_snapshot_matches(plan))
        self.assertEqual(stored_source.status, PlanStatus.APPLIED)
        self.assertEqual(cached.status, PlanStatus.AWAITING_APPROVAL)
        return source, {
            "plan_id": cached_id,
            "plan_hash": self.service.plan_hash(cached),
        }

    async def assert_refused(self, call, codes=None):
        with self.assertRaises(GovernanceError) as raised:
            result = call()
            if hasattr(result, "__await__"):
                await result
        if codes is not None:
            self.assertIn(raised.exception.code, codes)
        return raised.exception.code

    async def test_every_non_rollback_path_refuses_f2_v2_plans(self):
        source, cached = await self.seed_records()
        for record in (source, cached):
            with self.subTest(plan=record["plan_id"]):
                plan_id, plan_hash = record["plan_id"], record["plan_hash"]
                before = self.repository.get(plan_id)
                await self.assert_refused(
                    lambda: self.service._load(plan_id),
                    {ErrorCode.POLICY_SNAPSHOT_MISMATCH},
                )
                await self.assert_refused(
                    lambda: self.service.approve(plan_id, plan_hash),
                    {
                        ErrorCode.POLICY_SNAPSHOT_MISMATCH,
                        ErrorCode.PROHIBITED_CHANGE,
                    },
                )
                await self.assert_refused(
                    lambda: self.service.issue_external_csrf(
                        plan_id, "0" * 32
                    )
                )
                await self.assert_refused(
                    lambda: self.service.decide_external_approval(
                        plan_id=plan_id,
                        challenge_id="0" * 32,
                        expected_plan_hash=plan_hash,
                        approval_kind="external_approval",
                        approval_action="plan_approval",
                        csrf_nonce="0" * 32,
                        decision="approve",
                        approver_principal="home_assistant_admin_ingress:reviewer",
                    )
                )
                await self.assert_refused(
                    lambda: self.service.apply(plan_id, plan_hash)
                )
                after = self.repository.get(plan_id)
                self.assertEqual(after.status, before.status)
                self.assertEqual(after.policy_decision, before.policy_decision)
                self.assertEqual(after.approval.state, before.approval.state)
                self.assertTrue(persisted_f2_v2_policy_snapshot_matches(after))
        self.assertEqual(self.writes(), [])
        self.assertIsNone(self.service.task_repository.get_for_plan(cached["plan_id"]))

    async def test_rollback_change_without_f3_refuses_the_f2_v2_source(self):
        source, _cached = await self.seed_records()
        legacy = ChangeGovernanceService(
            self.repository,
            self.gateway,
            AuditLogger(str(self.audit_path), "synthetic-rollback-audit"),
            now=self.service.now,
        )
        self.assertIsNone(legacy.f3_runtime)
        await self.assert_refused(
            lambda: legacy.rollback_change(source["plan_id"], source["plan_hash"]),
            {ErrorCode.POLICY_SNAPSHOT_MISMATCH},
        )
        self.assertEqual(self.writes(), [])

    async def test_f3_rollback_admits_the_source_as_evidence_only(self):
        source, _cached = await self.seed_records()
        admitted = self.service._load(source["plan_id"], f3_rollback_source=True)
        self.assertEqual(admitted.policy_decision.policy_version, "f2-v2")
        result, request = await self.rollback(source)
        self.assertEqual(result["status"], "rollback_plan_created")
        inverse = self.repository.get(request["plan_id"])
        # The inverse carries current policy; the f2-v2 decision never
        # authorizes the new dispatch.
        self.assertEqual(inverse.policy_decision.policy_version, POLICY_VERSION)
        self.assertTrue(policy_snapshot_matches(inverse))
        # Creating the inverse granted the source no lasting authority.
        await self.assert_refused(
            lambda: self.service._load(source["plan_id"]),
            {ErrorCode.POLICY_SNAPSHOT_MISMATCH},
        )
        await self.assert_refused(
            lambda: self.service.apply(source["plan_id"], source["plan_hash"])
        )
        self.assertEqual(self.writes(), [])


# Keep only the tests defined in this module: drop the inherited rollback
# suite so it runs once, in its own module.
for _name_ in [
    name
    for name in dir(rollback.RollbackTests)
    if name.startswith("test_") and name not in BehavioralConfinementTests.__dict__
]:
    setattr(BehavioralConfinementTests, _name_, None)
del _name_
