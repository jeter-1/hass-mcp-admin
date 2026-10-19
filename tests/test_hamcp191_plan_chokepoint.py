"""HAMCP-191 correction batch 2: Codex H1g, H2 and H3 as permanent tests.

- H1g: a secret-bearing ``automation_id`` (a known secret, and a known
  secret wrapped in a forged marker) is refused before any record exists,
  and no identifier echo in response metadata carries it.
- H2: apply-time backup evidence carrying a wrapped known secret is
  redacted at the plan-persistence chokepoint; dispatch facts stay exact
  and a resumed apply never redispatches.
- Direction A: every plan-record write goes through
  ``ChangePlanRepository.save`` and its strict screen. A static inventory
  and a runtime spy over profiled governance tests fail on a bypassing writer;
  the exempt dispatch fields are proven unable to carry free text.
- H3: Core-log credentials spanning LF or CRLF line breaks, and a quoted
  multiline value, are redacted on the authenticated MCP path.

Every synthetic secret is built at runtime. No live Home Assistant.
"""

from __future__ import annotations

import ast
import builtins
import copy
from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import typing
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

import httpx  # noqa: E402
from aiohttp import web  # noqa: E402

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering import sanitization as S  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import models as M  # noqa: E402
from ha_mcp_engineering.governance import storage as ST  # noqa: E402
from ha_mcp_engineering.governance.operational import BackupAdministrationGateway  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
    PlanContentRefused,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools import (  # noqa: E402
    core_logs,
    get_registered_server,
    governance as governance_tools,
    registered_tools,
)

import test_2_1a_operational_backup as backup_fixture  # noqa: E402
import test_core_log_history  # noqa: E402  (module import: its tests are not re-collected here)
from test_hamcp191_families import (  # noqa: E402
    COLLISION,
    FORGED,
    PLAN_CURRENT,
    _GovernanceHarness,
    marker,
)

PACKAGE = ROOT / "hass_mcp_engineering_beta" / "ha_mcp_engineering"


def plan_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def disk_text(root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in plan_files(root))


async def run_tool(name: str, arguments: dict, service) -> str:
    with patch.object(governance_tools.GOVERNANCE, "require", return_value=service):
        tool = registered_tools(get_registered_server())[name]
        return await tool.run(arguments)


# ---------------------------------------------------------------------------
# H1g: identifiers at creation and in response metadata.


class IdentifierCreationTests(_GovernanceHarness):
    def proposed(self):
        value = copy.deepcopy(PLAN_CURRENT)
        value.pop("id", None)
        return value

    async def create_through_tool(self, automation_id: str) -> str:
        return await run_tool(
            "create_change_plan",
            {
                "title": "ordinary",
                "description": "ordinary",
                "operation": "create_automation",
                "automation_id": automation_id,
                "proposed_config": self.proposed(),
            },
            self.service,
        )

    async def test_secret_bearing_automation_ids_are_refused_and_never_echoed(self):
        # Codex H1g: the base persisted both records and echoed both IDs.
        secret = self.access.lower()
        self.service.sensitive_values = (secret,)
        for kind, automation_id in (
            ("valid_known_secret_id", secret),
            ("invalid_wrapped_id", marker(secret)),
        ):
            with self.subTest(kind=kind):
                rendered = await self.create_through_tool(automation_id)
                result = json.loads(rendered)
                self.assertFalse(result["success"])
                # Batch 3: the tool-boundary identifier screen refuses first.
                self.assertEqual(result["error_code"], ErrorCode.INVALID_REQUEST.value)
                self.assertEqual(
                    result["details"],
                    {"reason": governance_tools.IDENTIFIER_REFUSAL_REASON},
                )
                self.assertNotIn(secret, rendered)
                self.assertNotIn("resource_id", result.get("metadata", {}))
                self.assertEqual(self.repository.list(), [])
                self.assertEqual(plan_files(self.repository.root), [])
                self.assertEqual(self.gateway.writes, 0)
                # The service gate behind the boundary still refuses a
                # direct call without echo.
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.create_plan(
                        title="ordinary",
                        description="ordinary",
                        operation="create_automation",
                        automation_id=automation_id,
                        proposed_config=self.proposed(),
                    )
                self.assertEqual(raised.exception.code, ErrorCode.AUTOMATION_VALIDATION_FAILED)
                self.assertNotIn(secret, json.dumps(raised.exception.details))
                self.assertEqual(plan_files(self.repository.root), [])

    async def test_ordinary_automation_id_is_created_and_echoed(self):
        rendered = await run_tool(
            "create_change_plan",
            {
                "title": "HAMCP-191 fixture",
                "description": "Governed description-only update",
                "operation": "update_automation",
                "automation_id": "fixture",
                "proposed_config": {**self.proposed(), "description": "After"},
            },
            self.service,
        )
        result = json.loads(rendered)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["metadata"]["resource_id"], "fixture")
        self.assertEqual(len(self.repository.list()), 1)

    async def test_plan_id_echo_is_withheld_when_it_carries_a_known_secret(self):
        # A known secret that fits the plan-id format reaches only a lookup
        # miss; the response must not echo it.
        secret = hashlib.sha256(syn.needle("hamcp191 plan id echo").encode()).hexdigest()[:32]
        self.service.sensitive_values = (self.access, secret)
        for name, arguments in (
            ("get_change_plan", {"plan_id": secret}),
            ("approve_change_plan", {"plan_id": secret, "expected_plan_hash": "0" * 64}),
            ("apply_change_plan", {"plan_id": secret, "expected_plan_hash": "0" * 64}),
            ("rollback_change", {"plan_id": secret, "expected_plan_hash": "0" * 64}),
        ):
            with self.subTest(tool=name):
                rendered = await run_tool(name, arguments, self.service)
                self.assertNotIn(secret, rendered)
                self.assertNotIn("resource_id", json.loads(rendered).get("metadata", {}))
        ordinary = "0123456789abcdef" * 2
        rendered = await run_tool("get_change_plan", {"plan_id": ordinary}, self.service)
        self.assertEqual(json.loads(rendered)["metadata"]["resource_id"], ordinary)

    async def test_identifiers_are_withheld_without_a_governance_service(self):
        unavailable = Mock(side_effect=RuntimeError("governance unavailable"))
        with patch.object(governance_tools.GOVERNANCE, "require", unavailable):
            tool = registered_tools(get_registered_server())["get_change_plan"]
            rendered = await tool.run({"plan_id": "0123456789abcdef" * 2})
        result = json.loads(rendered)
        self.assertFalse(result["success"])
        self.assertNotIn("resource_id", result.get("metadata", {}))

    async def test_repository_refuses_a_secret_target_before_any_file_exists(self):
        created = await self.create()
        stored = self.repository.get(created["plan_id"])
        fresh = M.ChangePlan.from_dict(
            {**stored.to_dict(), "plan_id": "f" * 32,
             "target": {"target_type": "automation", "target_id": self.access}}
        )
        before = self.repository.content_refusals
        with self.assertRaises(PlanContentRefused) as raised:
            self.repository.save(fresh)
        self.assertTrue(raised.exception.creation)
        self.assertEqual(self.repository.content_refusals - before, 1)
        self.assertFalse((self.repository.root / ("f" * 32 + ".json")).exists())
        self.assertNotIn(self.access, disk_text(self.repository.root))

    async def test_strict_identifier_scan_refuses_a_forged_marker_target(self):
        # Batch 3: since L3 the D11 stored-plan check also runs for this
        # contract-v1 record and catches a known secret on its own. A forged
        # marker is tolerated by D11 (stored_plan=True), so only the strict
        # identifier scan refuses this target.
        forged = "[REDACTED:access_secret]"
        self.assertEqual(
            ST.persistence_safety_errors(
                {"target": {"target_id": forged}}, (), stored_plan=True
            ),
            [],
        )
        created = await self.create()
        stored = self.repository.get(created["plan_id"])
        fresh = M.ChangePlan.from_dict(
            {**stored.to_dict(), "plan_id": "e" * 32,
             "target": {"target_type": "automation", "target_id": forged}}
        )
        before = self.repository.content_refusals
        with self.assertRaises(PlanContentRefused) as raised:
            self.repository.save(fresh)
        self.assertTrue(raised.exception.creation)
        self.assertEqual(self.repository.content_refusals - before, 1)
        self.assertFalse((self.repository.root / ("e" * 32 + ".json")).exists())


# ---------------------------------------------------------------------------
# H2: apply-time evidence through the real backup gateway.


class _InventorySocket:
    """Synthetic backup/info: clean before dispatch, ``state`` after it."""

    def __init__(self, fake, state):
        self.fake = fake
        self.state = state

    async def command(self, arguments):
        assert arguments == {"type": "backup/info"}
        if not self.fake.dispatch_count:
            return {
                "backups": copy.deepcopy(self.fake.baseline["backups"]),
                "state": "idle",
                "last_action_event": {"state": "completed", "backup_id": "existing"},
            }
        return {
            "backups": [{
                "backup_id": "backup-new", "name": "Nightly safe backup",
                "date": "2026-07-26T12:00:01+00:00", "size_bytes": 100,
            }],
            "state": self.state,
            "last_action_event": {"state": "completed", "backup_id": "backup-new"},
        }


class _BackupProvider:
    def __init__(self, fake):
        self.fake = fake

    async def probe(self):
        return SimpleNamespace(as_dict=lambda: copy.deepcopy(self.fake.evidence))

    async def create_full_backup(self, name, *, before_dispatch):
        return await self.fake.create_full_backup(name, before_dispatch=before_dispatch)

    def health_snapshot(self):
        return self.fake.health_snapshot()


class ApplyTimeEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = backup_fixture.Clock()
        self.fake = backup_fixture.FakeOperationalGateway()
        self.access = syn.needle("hamcp191 h2 audit access")
        self.secret = syn.needle("hamcp191 h2 apply evidence").lower()
        self.repository = ChangePlanRepository(Path(self.temp.name) / "plans")
        self.socket = _InventorySocket(self.fake, marker(self.secret))
        self.service = ChangeGovernanceService(
            self.repository,
            backup_fixture.LegacyGateway(),
            AuditLogger(str(Path(self.temp.name) / "audit.jsonl"), self.access),
            now=self.clock,
            sensitive_values=(self.access, self.secret),
            operational_gateway=BackupAdministrationGateway(_BackupProvider(self.fake), self.socket),
        )
        self.telemetry, self.context = begin_request("hamcp191-h2-request")
        self.telemetry.caller_id = "mcp-requester"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    grant = backup_fixture.OperationalBackupLifecycleTests.grant

    def assert_clean(self, plan_id):
        saved = self.repository.get(plan_id)
        for label, value in (
            ("disk", disk_text(self.repository.root)),
            ("record", json.dumps(saved.to_dict())),
            ("evidence", json.dumps(saved.operational.verification.evidence)),
            ("get_plan", json.dumps(self.service.get_plan(plan_id))),
            ("review", json.dumps(self.service._review_summary(saved))),
        ):
            with self.subTest(surface=label):
                self.assertNotIn(self.secret, value)
        return saved

    async def test_wrapped_secret_in_apply_evidence_is_redacted_without_redispatch(self):
        created = await self.service.create_backup_plan(backup_name="Nightly safe backup")
        self.assertNotIn(self.secret, json.dumps(created))
        plan = await self.grant(created)
        redactions = self.repository.content_redactions
        # The tainted read-back cannot verify: verification stays required.
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.BACKUP_VERIFICATION_TIMEOUT)
        self.assertNotIn(self.secret, json.dumps(raised.exception.details or {}))
        self.assertEqual(self.fake.dispatch_count, 1)
        saved = self.assert_clean(plan["plan_id"])
        self.assertEqual(saved.status, M.PlanStatus.VERIFICATION_REQUIRED)
        self.assertGreater(self.repository.content_redactions - redactions, 0)
        self.assertIn(COLLISION, json.dumps(saved.operational.verification.evidence))
        # Dispatch facts are exempt and byte-exact: still one attempt.
        self.assertEqual(saved.operational.dispatch.get("attempt_count"), 1)

        # A resumed apply reads back again and never redispatches.
        with self.assertRaises(GovernanceError) as raised:
            await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertNotIn(self.secret, json.dumps(raised.exception.details or {}))
        self.assertEqual(self.fake.dispatch_count, 1)
        self.assert_clean(plan["plan_id"])

        # Useful success: a clean read-back completes the plan.
        self.socket.state = "idle"
        resolved = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(resolved["status"], "applied")
        self.assertEqual(self.fake.dispatch_count, 1)
        self.assert_clean(plan["plan_id"])


# ---------------------------------------------------------------------------
# Direction A: chokepoint units.


class ChokepointUnitTests(_GovernanceHarness):
    async def stored(self):
        created = await self.create()
        plan_id = created["plan_id"]
        return plan_id, self.repository._path(plan_id)

    async def test_update_redacts_new_evidence_in_place(self):
        plan_id, path = await self.stored()
        needle = syn.needle("hamcp191 update evidence")
        plan = self.repository.get(plan_id)
        plan.failure_information = {"reason": "provider said " + syn.bearer(needle)}
        before = self.repository.content_redactions
        self.repository.save(plan)
        self.assertEqual(self.repository.content_redactions - before, 1)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(needle, text)
        self.assertIn(marker("token"), text)
        # The caller's object equals the stored record.
        self.assertEqual(plan.to_dict(), json.loads(text))

    async def test_update_refuses_on_identity_paths_and_keeps_the_record(self):
        plan_id, path = await self.stored()
        original = path.read_bytes()
        needle = syn.needle("hamcp191 update title")
        for field_name, value in (
            ("title", "x " + syn.bearer(needle)),
            ("description", marker(self.access)),
        ):
            with self.subTest(field=field_name):
                plan = self.repository.get(plan_id)
                setattr(plan, field_name, value)
                with self.assertRaises(PlanContentRefused) as raised:
                    self.repository.save(plan)
                self.assertFalse(raised.exception.creation)
                self.assertEqual(path.read_bytes(), original)

    async def test_exempt_values_are_scanned_and_refused_never_rewritten(self):
        plan_id, path = await self.stored()
        original = path.read_bytes()
        fingerprint_secret = hashlib.sha256(syn.needle("hamcp191 exempt").encode()).hexdigest()
        self.service.sensitive_values = (self.access, fingerprint_secret)
        for field_name, value in (
            # A JWT fits the request-id alphabet; the strict scan finds it.
            ("apply_request_id", syn.jwt("hamcp191 exempt request id")),
            # A known secret in an exempt hash field.
            ("post_apply_fingerprint", fingerprint_secret),
            # A value outside the field's format.
            ("updated_at", "not a timestamp"),
        ):
            with self.subTest(field=field_name):
                plan = self.repository.get(plan_id)
                setattr(plan, field_name, value)
                with self.assertRaises(PlanContentRefused):
                    self.repository.save(plan)
                self.assertEqual(path.read_bytes(), original)

    async def test_old_stored_marker_content_is_kept_and_only_new_content_screened(self):
        plan_id, path = await self.stored()
        record = json.loads(path.read_text(encoding="utf-8"))
        record["description"] = "kept " + FORGED + " note"
        path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        repository = ChangePlanRepository(self.repository.root)
        plan = repository.get(plan_id)
        plan.updated_at = "2026-09-02T00:00:00+00:00"
        repository.save(plan)
        self.assertEqual(repository.get(plan_id).description, "kept " + FORGED + " note")
        # The same marker as new content on a redactable path collides.
        plan = repository.get(plan_id)
        plan.failure_information = {"reason": FORGED}
        repository.save(plan)
        self.assertEqual(repository.get(plan_id).failure_information, {"reason": COLLISION})

    async def test_refused_recovery_write_is_counted_and_startup_continues(self):
        plan_id, path = await self.stored()
        plan = self.repository.get(plan_id)
        plan.status = M.PlanStatus.APPLYING
        self.repository.save(plan)
        stored = path.read_bytes()
        repository = ChangePlanRepository(self.repository.root)
        self.assertEqual(repository.recover_incomplete("not a timestamp"), 0)
        self.assertEqual(repository.recovery_refusals, 1)
        self.assertEqual(path.read_bytes(), stored)
        self.assertEqual(repository.recover_incomplete("2026-09-02T00:00:00+00:00"), 1)
        self.assertEqual(repository.get(plan_id).status, M.PlanStatus.FAILED)


# ---------------------------------------------------------------------------
# Exempt-field schema proof.

_FORBIDDEN_IN_EXEMPT = (" ", "\t", "\n", "\r", "[", "]", "=", "@", "%", '"', "'", "/", "+")


def _valid_sample(name: str) -> str | None:
    return {
        "plan_id": "0123456789abcdef" * 2,
        "sha256": "0123456789abcdef" * 4,
        "timestamp": "2026-09-01T01:02:03.000004+00:00",
        "request_id": "hamcp191-request.01:a",
        "challenge_id": "Abc_def-0123456789",
    }.get(name)


def _unwrap(hint):
    args = [arg for arg in typing.get_args(hint) if arg is not type(None)]
    if typing.get_origin(hint) in (typing.Union, getattr(__import__("types"), "UnionType")):
        return args[0] if len(args) == 1 else hint
    return hint


def resolve_path(pattern: tuple[str, ...]):
    """Resolve an exempt path through the ChangePlan dataclasses.

    Returns ("leaf", type) for a typed str/Enum leaf, or ("mapping", key)
    when the path enters a ``dict[str, Any]`` field (receipts, caller context).
    """

    current = M.ChangePlan
    for index, segment in enumerate(pattern):
        current = _unwrap(current)
        if segment == "*":
            assert typing.get_origin(current) is list, pattern
            current = typing.get_args(current)[0]
            continue
        if typing.get_origin(current) is dict:
            assert index == len(pattern) - 1, pattern
            return ("mapping", segment)
        assert is_dataclass(current), (pattern, current)
        hints = typing.get_type_hints(current, vars(M))
        assert segment in {item.name for item in fields(current)}, (pattern, segment)
        current = hints[segment]
    return ("leaf", _unwrap(current))


MAPPING_EXEMPT_PATHS = {
    ("caller_context", "rollback_source_plan_id"),
    ("operations", "*", "execution_receipt", "provider_response_recorded_at"),
    ("operations", "*", "execution_receipt", "resulting_fingerprint"),
    *(
        ("operations", "*", "execution_receipt", f"{stage}_{side}_fingerprint")
        for stage in ("raw", "normalized", "binding")
        for side in ("approved", "observed")
    ),
}


class ExemptSchemaTests(unittest.TestCase):
    def test_every_exempt_path_resolves_to_a_typed_leaf_or_a_pinned_mapping_key(self):
        mapping = set()
        for pattern, name in ST.EXEMPT_FIELDS.items():
            with self.subTest(pattern=pattern):
                self.assertIn(name, ST.EXEMPT_FORMATS)
                kind, leaf = resolve_path(pattern)
                if kind == "mapping":
                    mapping.add(pattern)
                    continue
                if isinstance(leaf, type) and issubclass(leaf, Enum):
                    self.assertTrue(name.endswith(("status", "operation", "level", "state",
                                                   "class", "delta", "consequence", "kind")), name)
                else:
                    self.assertIs(leaf, str, (pattern, leaf))
        self.assertEqual(mapping, MAPPING_EXEMPT_PATHS)

    def test_format_validators_cannot_carry_free_text(self):
        for name, validator in ST.EXEMPT_FORMATS.items():
            sample = _valid_sample(name)
            if sample is None:
                continue
            with self.subTest(format=name):
                self.assertTrue(validator(sample))
                for character in _FORBIDDEN_IN_EXEMPT:
                    self.assertFalse(validator(sample[:4] + character + sample[5:]), character)
                    self.assertFalse(validator(sample + character), character)
                for text in (
                    FORGED, marker("x"), syn.bearer(syn.needle("hamcp191 exempt")),
                    "key=" + syn.needle("hamcp191 exempt assignment"), "",
                ):
                    self.assertFalse(validator(text))

    def test_enum_validators_accept_exactly_their_enum(self):
        enums = {
            "plan_status": (M.PlanStatus,), "change_operation": (M.ChangeOperation,),
            "risk_level": (M.RiskLevel,), "approval_state": (M.ApprovalState,),
            "policy_class": (M.ApprovalPolicyClass,), "risk_delta": (M.RiskDelta,),
            "physical_consequence": (M.PhysicalConsequence,),
            "approval_action_kind": (M.ApprovalActionKind,),
            "step_status": (M.StepExecutionStatus,),
        }
        self.assertEqual(
            set(ST.EXEMPT_FORMATS) - set(enums),
            {"plan_id", "sha256", "timestamp", "request_id", "challenge_id"},
        )
        for name, members in enums.items():
            validator = ST.EXEMPT_FORMATS[name]
            values = {item.value for enum in members for item in enum}
            with self.subTest(format=name):
                for value in values:
                    self.assertTrue(validator(value))
                    self.assertFalse(validator(value + " "))
                    self.assertFalse(validator(value.upper() + "x"))
                self.assertFalse(validator(FORGED))

    def test_a_jwt_fits_the_request_id_format_so_exempt_values_are_scanned(self):
        token = syn.jwt("hamcp191 request id format")
        self.assertTrue(ST.EXEMPT_FORMATS["request_id"](token))
        screen = S.screen_plan_content(
            {"apply_request_id": token}, baseline=None, exempt=ST.exempt_format,
        )
        self.assertTrue(screen.refused)


# ---------------------------------------------------------------------------
# Direction A: enumeration of every plan-record writer.

# Every ``.save(`` call in the package: (file, enclosing function, receiver).
# The two plan writers are marked True; a new caller fails until reviewed.
SAVE_CALLERS = {
    ("f3_runtime/repository.py", "ChildExecutionRepository._finish_initialization", "task_repository"): False,
    ("fan/service.py", "FanService._control", "self"): False,
    ("governance/service.py", "ChangeGovernanceService._save_task", "self.task_repository"): False,
    ("governance/service.py", "ChangeGovernanceService._save", "self.repository"): True,
    ("governance/storage.py", "ChangePlanRepository._recovery_save", "self"): True,
}
PLAN_SAVE_CALLERS = {
    "ChangeGovernanceService._save",
    "ChangePlanRepository._recovery_save",
}
# File writes in governance/storage.py: (function, call).
STORAGE_WRITES = {
    ("ChangePlanRepository.save", "open:x"),
    ("ChangePlanRepository.save", "os.replace"),
    ("ChangePlanRepository._quarantine", "os.replace"),
}


class _Calls(ast.NodeVisitor):
    def __init__(self, relative: str):
        self.relative = relative
        self.stack: list[str] = []
        self.saves: list[tuple[str, str, str]] = []
        self.writes: list[tuple[str, str, int]] = []
        self.screens: list[tuple[str, int]] = []

    def _scope(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _scope

    def visit_Call(self, node):
        scope = ".".join(self.stack)
        function = node.func
        if isinstance(function, ast.Attribute):
            receiver = ast.unparse(function.value)
            if function.attr == "save":
                self.saves.append((self.relative, scope, receiver))
            if function.attr == "_screen_new_content":
                self.screens.append((scope, node.lineno))
            if receiver == "os" and function.attr in {"replace", "rename", "link", "symlink"}:
                self.writes.append((scope, "os." + function.attr, node.lineno))
            # Path writes; str.replace takes two arguments, Path.replace one.
            if receiver != "os" and (
                function.attr in {"write_text", "write_bytes", "touch"}
                or (function.attr in {"rename", "replace"} and len(node.args) == 1)
            ):
                self.writes.append((scope, "path." + function.attr, node.lineno))
        if isinstance(function, ast.Name) and function.id == "open":
            mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
            if set(mode) & set("wxa+"):
                self.writes.append((scope, "open:" + mode, node.lineno))
        self.generic_visit(node)


def package_calls():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        visitor = _Calls(path.relative_to(PACKAGE).as_posix())
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        found.append(visitor)
    return found


class WriterInventoryTests(unittest.TestCase):
    def test_every_save_call_is_in_the_committed_table(self):
        saves = {save for visitor in package_calls() for save in visitor.saves}
        self.assertEqual(saves, set(SAVE_CALLERS))
        self.assertEqual(
            {scope for (_file, scope, _receiver), plan in SAVE_CALLERS.items() if plan},
            PLAN_SAVE_CALLERS,
        )

    def test_storage_writes_only_through_save_and_quarantine(self):
        storage = next(v for v in package_calls() if v.relative == "governance/storage.py")
        self.assertEqual({(scope, call) for scope, call, _line in storage.writes}, STORAGE_WRITES)
        # save screens the record before it opens the temporary file.
        screens = [line for scope, line in storage.screens if scope == "ChangePlanRepository.save"]
        opens = [line for scope, call, line in storage.writes
                 if scope == "ChangePlanRepository.save" and call == "open:x"]
        self.assertEqual(len(screens), 1)
        self.assertEqual(len(opens), 1)
        self.assertLess(screens[0], opens[0])


# Plan writes the runtime spy must observe: (package caller, operation,
# status). A profile of the seven governance suites the spy formerly ran
# whole (149 tests, about 1,240 s) observed exactly these; the governed
# rollback suite (about 1,190 s) added none of its own. SPY_TESTS is the
# cheapest set of tests that still reaches every one, so the spy fits in
# a CI shard and the coverage it claims is asserted, not assumed.
SPY_REQUIRED_WRITES = {
    ("ChangeGovernanceService._save", "configuration_plan", "applied"),
    ("ChangeGovernanceService._save", "configuration_plan", "applying"),
    ("ChangeGovernanceService._save", "configuration_plan", "approved"),
    ("ChangeGovernanceService._save", "configuration_plan", "awaiting_approval"),
    ("ChangeGovernanceService._save", "configuration_plan", "failed"),
    ("ChangeGovernanceService._save", "configuration_plan", "superseded"),
    ("ChangeGovernanceService._save", "configuration_plan", "verification_failed"),
    ("ChangeGovernanceService._save", "configuration_plan", "verification_required"),
    ("ChangeGovernanceService._save", "controlled_reload", "awaiting_approval"),
    ("ChangeGovernanceService._save", "create_automation", "awaiting_approval"),
    ("ChangeGovernanceService._save", "create_full_backup", "applied"),
    ("ChangeGovernanceService._save", "create_full_backup", "applying"),
    ("ChangeGovernanceService._save", "create_full_backup", "approved"),
    ("ChangeGovernanceService._save", "create_full_backup", "awaiting_approval"),
    ("ChangeGovernanceService._save", "create_full_backup", "expired"),
    ("ChangeGovernanceService._save", "create_full_backup", "failed"),
    ("ChangeGovernanceService._save", "create_full_backup", "rejected"),
    ("ChangeGovernanceService._save", "create_full_backup", "verification_failed"),
    ("ChangeGovernanceService._save", "create_full_backup", "verification_required"),
    ("ChangeGovernanceService._save", "restart_home_assistant", "awaiting_approval"),
    ("ChangeGovernanceService._save", "set_input_boolean_state", "awaiting_approval"),
    ("ChangeGovernanceService._save", "update_automation", "applied"),
    ("ChangeGovernanceService._save", "update_automation", "applying"),
    ("ChangeGovernanceService._save", "update_automation", "approved"),
    ("ChangeGovernanceService._save", "update_automation", "awaiting_approval"),
    ("ChangePlanRepository._recovery_save", "configuration_plan", "failed"),
    ("ChangePlanRepository._recovery_save", "create_full_backup", "verification_required"),
}
SPY_TESTS = (
    "test_2_1a_operational_backup.OperationalBackupLifecycleTests.test_definitive_provider_failure_is_terminal_without_verification",
    "test_2_1a_operational_backup.OperationalBackupLifecycleTests.test_existing_plan_contracts_do_not_gain_operational_fields",
    "test_2_1a_operational_backup.OperationalBackupLifecycleTests.test_expired_and_rejected_approvals_never_dispatch",
    "test_2_1a_operational_backup.OperationalBackupLifecycleTests.test_restart_recovery_requires_verification_without_dispatch",
    "test_2_1a_operational_backup.OperationalBackupLifecycleTests.test_verification_failure_is_terminal_and_not_rollbackable",
    "test_dev14_configuration_plans.ApplyTests.test_ambiguous_write_uses_semantic_readback_without_redispatch",
    "test_dev14_configuration_plans.ApplyTests.test_category_enriched_update_applies_without_writing_metadata",
    "test_dev14_configuration_plans.ApplyTests.test_real_readback_mismatch_retains_response_and_single_task",
    "test_dev14_configuration_plans.PlanCreationTests.test_long_same_prefix_values_are_complete_and_distinct",
    "test_dev14_configuration_plans.RestartRecoveryTests.test_restart_preserves_verified_receipt_and_never_resumes",
    "test_hamcp069a_entity_metadata_execution.AuthorizationStageTests.test_readback_failure_keeps_the_task_observing",
    "test_hamcp191_families.GovernanceFamilyTests.test_ordinary_plan_is_approved_and_applied",
    "test_hamcp191_families.OperationalCreationGateTests.test_helper_state_plan",
    "test_hamcp191_families.OperationalCreationGateTests.test_restart_and_reload_plans",
)


class RuntimeWriterSpyTests(unittest.TestCase):
    """Every plan file the package writes was produced by the screen.

    Package code may write into a plan root only from
    ``ChangePlanRepository.save`` (its temporary file, then ``os.replace``),
    and every replaced payload must be one the screen returned. Writes by
    open, ``Path.write_text`` or ``os.replace`` from any other package
    function are violations.
    """

    def test_every_plan_write_in_the_governance_suites_was_screened(self):
        screened: set[str] = set()
        observed: set[tuple[str, str, str]] = set()
        roots: set[Path] = set()
        violations: list[str] = []
        stats = {"saves": 0, "writes": 0}
        original_init = ChangePlanRepository.__init__
        original_save = ChangePlanRepository.save
        original_screen = ChangePlanRepository._screen_new_content
        original_replace = os.replace

        def init(repository, *args, **kwargs):
            original_init(repository, *args, **kwargs)
            roots.update({Path(repository.root).resolve(), Path(repository.operational_root).resolve()})

        def screen(repository, plan, path):
            value = original_screen(repository, plan, path)
            screened.add(json.dumps(value, sort_keys=True, separators=(",", ":")))
            return value

        def save(repository, plan):
            caller = sys._getframe(1).f_code
            if Path(caller.co_filename).resolve().is_relative_to(PACKAGE):
                if caller.co_qualname not in PLAN_SAVE_CALLERS:
                    violations.append("save called from " + caller.co_qualname)
                observed.add((caller.co_qualname, plan.operation.value, plan.status.value))
            stats["saves"] += 1
            return original_save(repository, plan)

        original_open = builtins.open
        original_io_open = io.open
        stdlib = Path(os.__file__).resolve().parent

        def package_writer():
            # The first caller outside the standard library (Path.write_text
            # reaches io.open through pathlib), if it is package code.
            frame = sys._getframe(2)
            while frame is not None and Path(frame.f_code.co_filename).resolve().is_relative_to(stdlib):
                frame = frame.f_back
            if frame is not None and Path(frame.f_code.co_filename).resolve().is_relative_to(PACKAGE):
                return frame.f_code.co_qualname
            return None

        def opener(original):
            def open_(file, mode="r", *args, **kwargs):
                if isinstance(file, (str, os.PathLike)) and set(str(mode)) & set("wxa+"):
                    caller = package_writer()
                    if (
                        caller is not None
                        and caller != "ChangePlanRepository.save"
                        and Path(file).resolve().parent in roots
                    ):
                        violations.append("plan root opened for writing by " + caller)
                return original(file, mode, *args, **kwargs)
            return open_

        def replace(source, destination, *args, **kwargs):
            result = original_replace(source, destination, *args, **kwargs)
            caller = sys._getframe(1).f_code
            target = Path(destination).resolve()
            if (
                Path(caller.co_filename).resolve().is_relative_to(PACKAGE)
                and target.parent in roots
                and target.suffix == ".json"
            ):
                stats["writes"] += 1
                if caller.co_qualname != "ChangePlanRepository.save":
                    violations.append("plan file written by " + caller.co_qualname)
                elif target.read_text(encoding="utf-8") not in screened:
                    violations.append("unscreened plan payload")
            return result

        suite = unittest.defaultTestLoader.loadTestsFromNames(SPY_TESTS)
        stream = io.StringIO()
        with patch.object(ChangePlanRepository, "__init__", init), patch.object(
            ChangePlanRepository, "save", save
        ), patch.object(ChangePlanRepository, "_screen_new_content", screen), patch(
            "os.replace", replace
        ), patch("builtins.open", opener(original_open)), patch("io.open", opener(original_io_open)):
            result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
        failed = [str(test) for test, _ in (*result.failures, *result.errors)]
        self.assertEqual(failed, [], stream.getvalue()[-4000:])
        self.assertEqual(result.testsRun, len(SPY_TESTS))
        self.assertEqual(sorted(SPY_REQUIRED_WRITES - observed), [])
        self.assertEqual({caller for caller, _operation, _status in observed}, PLAN_SAVE_CALLERS)
        self.assertGreaterEqual(stats["writes"], len(SPY_REQUIRED_WRITES))
        self.assertGreaterEqual(stats["saves"], stats["writes"])
        self.assertEqual(violations, [])


# ---------------------------------------------------------------------------
# H3: Core-log credentials spanning line breaks, on the authenticated path.


class CoreLogSpanningTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_core_log_history.CoreLogHistoryTests.asyncSetUp
    asyncTearDown = test_core_log_history.CoreLogHistoryTests.asyncTearDown

    async def invoke(self):
        async def app(scope, receive, send):
            rpc = json.loads((await receive())["body"])
            tool = registered_tools(get_registered_server())[rpc["params"]["name"]]
            rendered = await tool.run(rpc["params"]["arguments"])
            body = {
                "jsonrpc": "2.0", "id": rpc["id"],
                "result": {"content": [{"type": "text", "text": rendered}], "isError": False},
            }
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": json.dumps(body).encode()})

        audit = tempfile.TemporaryDirectory()
        self.addCleanup(audit.cleanup)
        gateway = AuthenticatedMcpGateway(
            app, self.settings,
            AuditLogger(str(Path(audit.name) / "audit.jsonl"), self.settings.access_secret),
            core_runtime=SimpleNamespace(acquire=Mock(return_value=None)),
        )
        with patch.object(core_logs, "READER", self.reader), patch.object(core_logs, "SETTINGS", self.settings):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:8100"
            ) as client:
                response = await client.post(
                    "/" + self.settings.access_secret + "/mcp",
                    json={"jsonrpc": "2.0", "id": "hamcp191-core", "method": "tools/call",
                          "params": {"name": "get_core_log_history",
                                     "arguments": {"limit": 20, "offset": 0}}},
                )
        self.assertEqual(response.status_code, 200)
        return json.loads(response.json()["result"]["content"][0]["text"])

    async def test_credentials_spanning_line_breaks_are_redacted(self):
        # Codex H3: the 89d6ed4 per-line pass returned all three.
        needle = syn.needle("hamcp191 core spanning")
        for name, body in (
            ("anchor_lf", syn.bearer("") + "\n" + needle + "\n"),
            ("anchor_crlf", syn.bearer("") + "\r\n" + needle + "\r\n"),
            ("quoted_multiline", "pass" + "word=\"ordinary-prefix\n" + needle + "\"\n"),
            ("same_line", syn.bearer(needle) + "\n"),
            ("same_line_crlf", syn.bearer(needle) + "\r\n"),
        ):
            with self.subTest(case=name):
                self.response = web.Response(text=body, content_type="text/plain")
                result = await self.invoke()
                self.assertTrue(result["success"], result)
                self.assertNotIn(needle, json.dumps(result))
                self.assertTrue(result["data"]["redaction_applied"])
                self.assertEqual(result["data"]["collided_line_count"], 0)

    async def test_collision_stays_line_local_across_crlf(self):
        needle = syn.needle("hamcp191 core crlf marker")
        body = "ordinary\r\n" + marker(needle) + "\r\nHA **REDACTED**\r\n"
        self.response = web.Response(text=body, content_type="text/plain")
        result = await self.invoke()
        log = result["data"]["log"]
        self.assertNotIn(needle, json.dumps(result))
        self.assertIn("ordinary\r\n", log)
        self.assertIn("HA **REDACTED**\r\n", log)
        self.assertEqual(log, "ordinary\r\n" + COLLISION + "\r\nHA **REDACTED**\r\n")
        self.assertEqual(result["data"]["collided_line_count"], 1)


if __name__ == "__main__":
    unittest.main()
