"""HAMCP-191 correction batch 3: Antigravity H4, H1g-residual, M2 and L3.

- H4: a token-shaped inbound request ID (a JWT, a Matter payload, a
  ``name=value`` assignment or a configured secret) is replaced once, at
  request start, by an Engineering-generated ID. The apply and cancel
  reproductions leave no copy on disk or in the execution-task reads; only a
  count of replacements is kept. Ordinary IDs pass through unchanged.
- H1g-residual: every caller-supplied identifier of every governance tool is
  screened at the tool boundary before any tool work, so the helper-state and
  entity-metadata ``no_change`` shortcuts cannot echo a secret. An
  enumeration test classifies every tool input and proves the screen covers
  each identifier.
- M2: a refused dashboard plan leaves no dashboard artifact: url_path and the
  caller ID pass the pre-read gate, and the artifact is written only after the
  complete plan passes the strict creation gate.
- L3: the D11 stored-plan re-check runs for contract-v1 records too.

Every synthetic secret is built at runtime. No live Home Assistant.
"""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

import httpx  # noqa: E402

import hamcp162_synthetic as syn  # noqa: E402
from ha_mcp_engineering import request_context as RC  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration  # noqa: E402
from ha_mcp_engineering.governance.operational import BackupAdministrationGateway  # noqa: E402
from ha_mcp_engineering.governance.service import ChangeGovernanceService  # noqa: E402
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
    PlanContentRefused,
)
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.routing import AuthenticatedMcpGateway  # noqa: E402
from ha_mcp_engineering.tools import governance as governance_tools  # noqa: E402

import test_2_1a_operational_backup as backup_fixture  # noqa: E402
import test_dashboard_update_mvp as dashboard_fixture  # noqa: E402
from tests.hamcp069a_entity_fixtures import EntityMetadataServiceTestCase  # noqa: E402
from tests.test_beta37_exact_helper_state import (  # noqa: E402
    FakeDependencyRiskReader,
    FakeHelperStateGateway,
)
from test_hamcp191_families import _GovernanceHarness, plain_settings  # noqa: E402
from test_hamcp191_plan_chokepoint import (  # noqa: E402
    _BackupProvider,
    _InventorySocket,
    disk_text,
    plan_files,
    run_tool,
)

REFUSAL = {"reason": governance_tools.IDENTIFIER_REFUSAL_REASON}


def token_shaped_ids() -> dict[str, str]:
    """Request IDs that fit REQUEST_ID_PATTERN but carry a credential shape."""

    return {
        "jwt": syn.jwt("hamcp191 h4 request"),
        "matter_payload": syn.matter_payload(syn.needle("hamcp191 h4 matter")),
        "token_assignment": syn.assign("token", syn.needle("hamcp191 h4 assign"), ":"),
    }


# ---------------------------------------------------------------------------
# H4: the request ID is screened once, at request start.


class RequestIdScreenTests(unittest.TestCase):
    def begin(self, candidate, **kwargs):
        telemetry, token = begin_request(candidate, **kwargs)
        self.addCleanup(end_request, token)
        return telemetry

    def test_ordinary_request_ids_pass_through_unchanged(self):
        before = RC.request_id_replacement_count()
        for candidate in (uuid.uuid4().hex, str(uuid.uuid4()), "hamcp191-request.01:a"):
            with self.subTest(candidate=candidate):
                telemetry = self.begin(candidate, known_secrets=(syn.needle("hamcp191 h4 known"),))
                self.assertEqual(telemetry.request_id, candidate)
                self.assertFalse(telemetry.inbound_request_id_replaced)
        self.assertEqual(RC.request_id_replacement_count(), before)

    def test_token_shaped_request_ids_are_replaced_and_counted(self):
        for kind, candidate in token_shaped_ids().items():
            with self.subTest(kind=kind):
                self.assertRegex(candidate, RC.REQUEST_ID_PATTERN)
                before = RC.request_id_replacement_count()
                telemetry = self.begin(candidate)
                self.assertNotEqual(telemetry.request_id, candidate)
                self.assertRegex(telemetry.request_id, r"^[0-9a-f]{32}$")
                self.assertTrue(telemetry.inbound_request_id_replaced)
                self.assertEqual(RC.current_request_id(), telemetry.request_id)
                self.assertEqual(RC.request_id_replacement_count() - before, 1)
                self.assertNotIn(candidate, repr(telemetry))

    def test_a_configured_secret_is_replaced_only_when_known(self):
        secret = syn.needle("hamcp191 h4 configured")
        self.assertEqual(self.begin(secret).request_id, secret)
        before = RC.request_id_replacement_count()
        telemetry = self.begin(secret, known_secrets=(secret,))
        self.assertNotEqual(telemetry.request_id, secret)
        self.assertTrue(telemetry.inbound_request_id_replaced)
        self.assertEqual(RC.request_id_replacement_count() - before, 1)

    def test_malformed_ids_are_regenerated_without_counting(self):
        before = RC.request_id_replacement_count()
        for candidate in (None, "", "short", "bad id with spaces"):
            with self.subTest(candidate=candidate):
                telemetry = self.begin(candidate)
                self.assertRegex(telemetry.request_id, r"^[0-9a-f]{32}$")
                self.assertFalse(telemetry.inbound_request_id_replaced)
        self.assertEqual(RC.request_id_replacement_count(), before)


class ExecutionTaskRequestIdTests(unittest.IsolatedAsyncioTestCase):
    """Antigravity H4 reproductions through the real backup lifecycle."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = backup_fixture.Clock()
        self.fake = backup_fixture.FakeOperationalGateway()
        self.access = syn.needle("hamcp191 h4 audit access")
        self.repository = ChangePlanRepository(self.root / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            backup_fixture.LegacyGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), self.access),
            now=self.clock,
            sensitive_values=(self.access,),
            operational_gateway=BackupAdministrationGateway(
                _BackupProvider(self.fake), _InventorySocket(self.fake, "idle")
            ),
        )
        self.telemetry, self.context = begin_request("hamcp191-h4-request")
        self.telemetry.caller_id = "mcp-requester"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    grant = backup_fixture.OperationalBackupLifecycleTests.grant

    def under_request(self, candidate):
        end_request(self.context)
        self.telemetry, self.context = begin_request(candidate)
        self.telemetry.caller_id = "mcp-requester"
        return self.telemetry

    def all_text(self) -> str:
        audit = self.root / "audit.jsonl"
        return disk_text(self.root) + (audit.read_text(encoding="utf-8") if audit.exists() else "")

    def assert_absent(self, secret, task_id):
        for label, value in (
            ("disk", self.all_text()),
            ("get_execution_task", json.dumps(self.service.get_execution_task(task_id))),
            ("list_execution_tasks", json.dumps(self.service.list_execution_tasks())),
        ):
            with self.subTest(surface=label):
                self.assertNotIn(secret, value)

    async def test_apply_under_a_token_shaped_request_id_persists_no_copy(self):
        # Antigravity H4 repro 1: the task stored execution_request_id and
        # lifecycle request_id from the JWT before the plan save refused it.
        secret = token_shaped_ids()["jwt"]
        plan = await self.grant(await self.service.create_backup_plan(backup_name="Nightly safe backup"))
        telemetry = self.under_request(secret)
        self.assertTrue(telemetry.inbound_request_id_replaced)
        applied = await self.service.apply(plan["plan_id"], plan["plan_hash"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self.fake.dispatch_count, 1)
        tasks = self.service.list_execution_tasks()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assert_absent(secret, tasks[0]["task_id"])
        # The generated ID is the one recorded.
        self.assertIn(telemetry.request_id, json.dumps(self.service.get_execution_task(tasks[0]["task_id"])))

    async def test_cancel_under_a_token_shaped_request_id_persists_no_copy(self):
        # Antigravity H4 repro 2: the rejection event carried the request ID.
        plan = await self.grant(await self.service.create_backup_plan(backup_name="Nightly safe backup"))
        await self.service.apply(plan["plan_id"], plan["plan_hash"])
        task_id = self.service.list_execution_tasks()["tasks"][0]["task_id"]
        for kind, secret in token_shaped_ids().items():
            with self.subTest(kind=kind):
                self.under_request(secret)
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.cancel_execution_task(task_id)
                self.assertEqual(
                    raised.exception.code, ErrorCode.CANCELLATION_NOT_PERMITTED_AFTER_DISPATCH
                )
                self.assertNotIn(secret, json.dumps(raised.exception.details))
                self.assert_absent(secret, task_id)
        events = self.service.get_execution_task(task_id)["lifecycle_events"]
        self.assertEqual(
            sum(event.get("event_type") == "task_cancellation_rejected" for event in events),
            len(token_shaped_ids()),
        )

    async def test_ordinary_request_id_is_recorded_unchanged(self):
        ordinary = "hamcp191-h4-ordinary-01"
        plan = await self.grant(await self.service.create_backup_plan(backup_name="Nightly safe backup"))
        self.under_request(ordinary)
        await self.service.apply(plan["plan_id"], plan["plan_hash"])
        task = self.service.get_execution_task(self.service.list_execution_tasks()["tasks"][0]["task_id"])
        self.assertIn(ordinary, json.dumps(task))


class GatewayRequestIdTests(unittest.IsolatedAsyncioTestCase):
    """The authenticated MCP gateway adopts only a screened request ID."""

    async def send(self, request_id):
        seen = {}

        async def app(scope, receive, send):
            await receive()
            seen["request_id"] = RC.current_request_id()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b"{}"})

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = plain_settings()
        audit = Path(temp.name) / "audit.jsonl"
        gateway = AuthenticatedMcpGateway(
            app, settings, AuditLogger(str(audit), settings.access_secret),
            core_runtime=type("Core", (), {"acquire": Mock(return_value=None)})(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway), base_url="http://127.0.0.1:8100"
        ) as client:
            response = await client.post(
                "/" + settings.access_secret + "/mcp",
                headers={"x-request-id": request_id},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
        text = audit.read_text(encoding="utf-8") if audit.exists() else ""
        return response, seen, text, settings

    async def test_token_shaped_header_is_replaced(self):
        for kind, secret in token_shaped_ids().items():
            with self.subTest(kind=kind):
                before = RC.request_id_replacement_count()
                response, seen, audit, _ = await self.send(secret)
                self.assertEqual(response.status_code, 200)
                self.assertNotEqual(seen["request_id"], secret)
                self.assertRegex(seen["request_id"], r"^[0-9a-f]{32}$")
                self.assertNotIn(secret, json.dumps(dict(response.headers)))
                self.assertNotIn(secret, audit)
                self.assertEqual(RC.request_id_replacement_count() - before, 1)

    async def test_configured_secret_header_is_replaced(self):
        settings = plain_settings()
        before = RC.request_id_replacement_count()
        response, seen, audit, _ = await self.send(settings.ha_token)
        self.assertNotEqual(seen["request_id"], settings.ha_token)
        self.assertNotIn(settings.ha_token, json.dumps(dict(response.headers)) + audit)
        self.assertEqual(RC.request_id_replacement_count() - before, 1)

    async def test_ordinary_header_is_kept(self):
        ordinary = str(uuid.uuid4())
        before = RC.request_id_replacement_count()
        response, seen, _, _ = await self.send(ordinary)
        self.assertEqual(seen["request_id"], ordinary)
        self.assertEqual(response.headers.get("x-request-id"), ordinary)
        self.assertEqual(RC.request_id_replacement_count(), before)


# ---------------------------------------------------------------------------
# H1g-residual: identifiers at the tool boundary.


class HelperStateNoChangeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.secret = syn.needle("hamcp191 helper identifier").lower()
        self.helper = FakeHelperStateGateway()
        self.helper.state = "on"
        self.repository = ChangePlanRepository(root / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            backup_fixture.LegacyGateway(),
            AuditLogger(str(root / "audit.jsonl"), syn.needle("hamcp191 helper audit")),
            sensitive_values=(self.secret,),
            helper_state_gateway=self.helper,
            helper_dependency_risk_reader=FakeDependencyRiskReader(self.helper.entity_id),
        )
        self.telemetry, self.context = begin_request("hamcp191-helper-request")

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    async def test_secret_entity_id_is_refused_before_the_no_change_echo(self):
        # Antigravity H1g-residual repro 1: already "on", so the service
        # returned no_change with target_id echoing the entity_id.
        self.helper.entity_id = "input_boolean." + self.secret
        with patch.object(self.service, "create_helper_state_plan", wraps=self.service.create_helper_state_plan) as spy:
            rendered = await run_tool(
                "create_helper_state_plan",
                {"entity_id": self.helper.entity_id, "desired_state": "on"},
                self.service,
            )
        result = json.loads(rendered)
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], ErrorCode.INVALID_REQUEST.value)
        self.assertEqual(result["details"], REFUSAL)
        self.assertNotIn(self.secret, rendered)
        spy.assert_not_called()
        self.assertEqual(plan_files(self.repository.root), [])

    async def test_ordinary_entity_id_still_returns_no_change(self):
        rendered = await run_tool(
            "create_helper_state_plan",
            {"entity_id": self.helper.entity_id, "desired_state": "on"},
            self.service,
        )
        result = json.loads(rendered)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["status"], "no_change")
        self.assertEqual(result["data"]["target_id"], self.helper.entity_id)


class EntityMetadataNoChangeTests(EntityMetadataServiceTestCase):
    async def test_secret_entity_id_is_refused_before_the_no_change_echo(self):
        # Antigravity H1g-residual repro 2: already named, so the planner
        # returned no_change with target_id echoing the entity_id.
        secret = syn.needle("hamcp191 metadata identifier").lower()
        self.service.sensitive_values = (secret,)
        entity_id = "light." + secret
        self.add_entity(entity_id, name="Already Set")
        self.assertEqual((await self.create(entity_id=entity_id, name="Already Set"))["target_id"], entity_id)
        with patch.object(
            self.service, "create_entity_metadata_plan", wraps=self.service.create_entity_metadata_plan
        ) as spy:
            rendered = await run_tool(
                "create_entity_metadata_plan", {"entity_id": entity_id, "name": "Already Set"}, self.service
            )
        result = json.loads(rendered)
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], ErrorCode.INVALID_REQUEST.value)
        self.assertEqual(result["details"], REFUSAL)
        self.assertNotIn(secret, rendered)
        spy.assert_not_called()
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.ws.updates(), [])

    async def test_ordinary_entity_id_still_returns_no_change(self):
        self.add_entity("light.porch", name="Already Set")
        rendered = await run_tool(
            "create_entity_metadata_plan", {"entity_id": "light.porch", "name": "Already Set"}, self.service
        )
        result = json.loads(rendered)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["status"], "no_change")
        self.assertEqual(result["data"]["target_id"], "light.porch")


# Inputs that are not identifiers: closed literals, bounded numbers, the
# opaque server-issued cursor, and free text or configuration that the plan
# persistence chokepoint screens (and that no early return echoes).
NON_IDENTIFIER_INPUTS: dict[str, frozenset[str]] = {
    "create_backup_plan": frozenset({"backup_name", "expiration_minutes"}),
    "create_reload_plan": frozenset({"reload_target", "expiration_minutes"}),
    "create_helper_state_plan": frozenset({"desired_state", "expiration_minutes"}),
    "create_entity_metadata_plan": frozenset(
        {"name", "icon", "aliases", "hidden", "expiration_minutes"}
    ),
    # HAMCP-069B: operation and assistant are closed enums.
    "create_entity_availability_exposure_plan": frozenset(
        {"operation", "assistant", "expiration_minutes"}
    ),
    # HAMCP-165: operation is a closed enum; name, colour, icon and
    # description are schema-bounded label metadata.
    "create_label_plan": frozenset(
        {
            "operations[].operation", "operations[].name", "operations[].color",
            "operations[].icon", "operations[].description", "expiration_minutes",
        }
    ),
    # HAMCP-155: deletions[].* are identifiers; reason is screened text.
    "create_configuration_delete_plan": frozenset({"reason", "expiration_minutes"}),
    # HAMCP-145: option is the exact, schema-bounded option text; reason is
    # screened text.
    "create_input_select_option_plan": frozenset({"option", "reason", "expiration_minutes"}),
    # HAMCP-232: changes[].entity_id is an identifier; set and clear name
    # only the closed editable field vocabulary and its validated values.
    "create_helper_options_plan": frozenset(
        {"changes[].set", "changes[].clear", "reason", "expiration_minutes"}
    ),
    # HAMCP-106: url_path is the identifier; title and icon are
    # schema-bounded metadata and the rest are booleans or bounded numbers.
    "create_dashboard_metadata_plan": frozenset(
        {"title", "icon", "show_in_sidebar", "require_admin", "expiration_minutes"}
    ),
    # HAMCP-071: config_id and entity_id are identifiers; name and icon are
    # schema-bounded metadata, entities is the member patch the planner
    # re-validates, and expiration_minutes is a bounded number.
    "create_scene_configuration_plan": frozenset(
        {"name", "icon", "entities", "expiration_minutes"}
    ),
    "create_addon_restart_plan": frozenset({"expiration_minutes"}),
    "create_home_assistant_restart_plan": frozenset({"expiration_minutes"}),
    "create_change_plan": frozenset(
        {"title", "description", "proposed_config", "expiration_minutes", "caller_context"}
    ),
    "create_configuration_plan": frozenset(
        {"title", "description", "expiration_minutes", "caller_context", "operations[].proposed_config"}
    ),
    "create_dashboard_update_plan": frozenset(
        {"title", "description", "expiration_minutes", "patch_operations[].value"}
    ),
    "get_change_plan": frozenset({"detail_section", "cursor", "page_size"}),
    "list_change_plans": frozenset({"limit"}),
    "get_execution_task": frozenset(),
    "list_execution_tasks": frozenset({"limit"}),
    "cancel_execution_task": frozenset(),
    "reverify_configuration_task": frozenset(),
    "approve_change_plan": frozenset({"approval_note"}),
    "apply_change_plan": frozenset(),
    "rollback_change": frozenset(),
}

class _ServedDeletionItem:
    """HAMCP-155 serves an explicit closed item schema; mirror its keys."""

    __annotations__ = dict.fromkeys(
        governance_tools.CONFIGURATION_DELETE_INPUT_SCHEMA["properties"]["deletions"]["items"][
            "properties"
        ],
        str,
    )


class _ServedHelperOptionsChange:
    """HAMCP-232 serves an explicit closed item schema; mirror its keys."""

    __annotations__ = dict.fromkeys(
        governance_tools.HELPER_OPTIONS_INPUT_SCHEMA["properties"]["changes"]["items"][
            "properties"
        ],
        str,
    )


ITEM_TYPES = {
    "operations": governance_tools.ConfigurationOperation,
    "patch_operations": governance_tools.DashboardPatchOperation,
    "deletions": _ServedDeletionItem,
    "changes": _ServedHelperOptionsChange,
}

PLAN_ID = "0123456789abcdef" * 2
TASK_ID = "fedcba9876543210" * 2
PLAN_HASH = "ab" * 32

ORDINARY_ARGUMENTS: dict[str, dict] = {
    "create_backup_plan": {"backup_name": "Nightly safe backup"},
    "create_reload_plan": {"reload_target": "automation"},
    "create_helper_state_plan": {"entity_id": "input_boolean.porch", "desired_state": "on"},
    "create_entity_metadata_plan": {
        "entity_id": "light.porch", "name": "Porch", "area_id": "porch",
        "add_labels": ["night"], "remove_labels": ["outdoor"], "categories": {"automation": "c1"},
    },
    "create_entity_availability_exposure_plan": {
        "operation": "expose", "entity_ids": ["light.porch"], "assistant": "conversation",
    },
    "create_label_plan": {
        "operations": [
            {"operation": "update", "label_id": "night", "name": "Night", "entity_ids": ["light.porch"]}
        ],
    },
    "create_configuration_delete_plan": {
        "deletions": [{"resource_type": "automation", "target_id": "porch_light"}],
        "reason": "cleanup",
    },
    "create_input_select_option_plan": {
        "entity_id": "input_select.porch_mode", "option": "Away", "reason": "ordinary",
    },
    "create_helper_options_plan": {
        "changes": [{"entity_id": "sensor.porch_filtered", "set": {"precision": 1}}],
        "reason": "ordinary",
    },
    "create_dashboard_metadata_plan": {"url_path": "main-operations", "title": "Main Operations"},
    "create_scene_configuration_plan": {
        "config_id": "1700000000001",
        "entity_id": "scene.evening",
        "name": "Evening",
    },
    "create_addon_restart_plan": {"addon_slug": "core_mosquitto"},
    "create_home_assistant_restart_plan": {},
    "create_change_plan": {
        "title": "ordinary", "description": "ordinary", "operation": "update_automation",
        "automation_id": "fixture", "proposed_config": {"alias": "Fixture"},
    },
    "create_configuration_plan": {
        "title": "ordinary", "description": "ordinary",
        "operations": [{
            "operation_id": "update", "resource_type": "helper", "action": "update",
            "target_id": "porch", "proposed_config": {"name": "Porch"},
            "helper_type": "input_boolean", "depends_on": ["first"],
        }],
    },
    "create_dashboard_update_plan": {
        "title": "ordinary", "description": "ordinary", "url_path": "main-operations",
        "patch_operations": [{"operation_id": "rename", "operation": "replace", "path": "/title", "value": "After"}],
    },
    "get_change_plan": {"plan_id": PLAN_ID},
    "list_change_plans": {"status": "awaiting_approval"},
    "get_execution_task": {"task_id": TASK_ID},
    "list_execution_tasks": {"state": "created", "terminal_outcome": "completed", "plan_id": PLAN_ID},
    "cancel_execution_task": {"task_id": TASK_ID},
    "reverify_configuration_task": {
        "task_id": TASK_ID, "expected_plan_hash": PLAN_HASH, "request_id": str(uuid.UUID(int=7)),
    },
    "approve_change_plan": {"plan_id": PLAN_ID, "expected_plan_hash": PLAN_HASH},
    "apply_change_plan": {"plan_id": PLAN_ID, "expected_plan_hash": PLAN_HASH},
    "rollback_change": {"plan_id": PLAN_ID, "expected_plan_hash": PLAN_HASH},
}


def injected(arguments: dict, path: str, secret: str) -> list[dict]:
    """Copies of ``arguments`` with ``secret`` at ``path`` (value and key forms)."""

    name, _, field = path.partition("[].")
    base = copy.deepcopy(arguments)
    if field:
        target, container = base[name][0], field
    else:
        target, container = base, name
    current = target.get(container)
    if isinstance(current, list):
        target[container] = [secret]
        return [base]
    if isinstance(current, dict):
        keyed = copy.deepcopy(base)
        (keyed[name][0] if field else keyed)[container] = {secret: "c1"}
        target[container] = {key: secret for key in current}
        return [base, keyed]
    target[container] = secret
    return [base]


class IdentifierBoundaryEnumerationTests(unittest.IsolatedAsyncioTestCase):
    def tools(self):
        return {tool.__name__: tool for tool in governance_tools.GOVERNANCE_TOOLS}

    def test_every_tool_input_is_classified(self):
        tools = self.tools()
        self.assertEqual(set(governance_tools.IDENTIFIER_INPUTS), set(tools))
        self.assertEqual(set(NON_IDENTIFIER_INPUTS), set(tools))
        self.assertEqual(set(ORDINARY_ARGUMENTS), set(tools))
        for name, tool in tools.items():
            with self.subTest(tool=name):
                identifiers = set(governance_tools.IDENTIFIER_INPUTS[name])
                other = NON_IDENTIFIER_INPUTS[name]
                self.assertFalse(identifiers & other)
                expected = set()
                for parameter in inspect.signature(tool).parameters:
                    if name == "create_label_plan" and parameter == "operations":
                        # HAMCP-165: the label items are closed by the served
                        # schema, not by the configuration item type.
                        expected |= {
                            f"{parameter}[].{key}"
                            for key in governance_tools.LABEL_PLAN_INPUT_SCHEMA[
                                "properties"
                            ]["operations"]["items"]["properties"]
                        }
                    elif parameter in ITEM_TYPES:
                        expected |= {
                            f"{parameter}[].{key}"
                            for key in ITEM_TYPES[parameter].__annotations__
                        }
                    else:
                        expected.add(parameter)
                self.assertEqual(identifiers | other, expected)
        # The served entity-metadata schema has no input the direct
        # signature lacks.
        self.assertEqual(
            set(governance_tools.ENTITY_METADATA_INPUT_SCHEMA["properties"]),
            set(inspect.signature(tools["create_entity_metadata_plan"]).parameters),
        )
        self.assertEqual(
            set(governance_tools.ENTITY_AVAILABILITY_EXPOSURE_INPUT_SCHEMA["properties"]),
            set(inspect.signature(tools["create_entity_availability_exposure_plan"]).parameters),
        )
        self.assertEqual(
            set(governance_tools.LABEL_PLAN_INPUT_SCHEMA["properties"]),
            set(inspect.signature(tools["create_label_plan"]).parameters),
        )
        self.assertEqual(
            set(governance_tools.CONFIGURATION_DELETE_INPUT_SCHEMA["properties"]),
            set(inspect.signature(tools["create_configuration_delete_plan"]).parameters),
        )
        self.assertEqual(
            set(governance_tools.INPUT_SELECT_OPTION_INPUT_SCHEMA["properties"]),
            set(inspect.signature(tools["create_input_select_option_plan"]).parameters),
        )
        self.assertEqual(
            set(governance_tools.HELPER_OPTIONS_INPUT_SCHEMA["properties"]),
            set(inspect.signature(tools["create_helper_options_plan"]).parameters),
        )
        self.assertEqual(
            set(governance_tools.DASHBOARD_METADATA_INPUT_SCHEMA["properties"]),
            set(inspect.signature(tools["create_dashboard_metadata_plan"]).parameters),
        )
        self.assertEqual(
            set(governance_tools.SCENE_CONFIGURATION_INPUT_SCHEMA["properties"]),
            set(inspect.signature(tools["create_scene_configuration_plan"]).parameters),
        )

    def service(self, secret):
        service = Mock()
        service.sensitive_values = (secret,)
        return service

    async def call(self, name, arguments, service):
        tool = self.tools()[name]
        with patch.object(governance_tools.GOVERNANCE, "require", return_value=service):
            if name == "create_dashboard_metadata_plan":
                # HAMCP-106: the served tool passes the raw argument object
                # (field presence matters); the direct function refuses.
                return await governance_tools.registered_dashboard_metadata_tool().run(arguments)
            if name == "create_scene_configuration_plan":
                # HAMCP-071: the same raw-argument pattern as HAMCP-106.
                return await governance_tools.registered_scene_configuration_tool().run(arguments)
            return await tool(**arguments)

    async def test_every_identifier_input_is_screened_before_tool_work(self):
        known = syn.needle("hamcp191 boundary identifier").lower()
        shapes = {"known_secret": known, "jwt": syn.jwt("hamcp191 boundary identifier")}
        for name, paths in governance_tools.IDENTIFIER_INPUTS.items():
            for path in paths:
                for shape, secret in shapes.items():
                    for arguments in injected(ORDINARY_ARGUMENTS[name], path, secret):
                        with self.subTest(tool=name, path=path, shape=shape, arguments=sorted(arguments)):
                            service = self.service(known)
                            rendered = await self.call(name, arguments, service)
                            result = json.loads(rendered)
                            self.assertFalse(result["success"])
                            self.assertEqual(result["error_code"], ErrorCode.INVALID_REQUEST.value)
                            self.assertEqual(result["details"], REFUSAL)
                            self.assertNotIn(secret, rendered)
                            self.assertEqual(service.method_calls, [])

    async def test_ordinary_identifiers_reach_the_service(self):
        known = syn.needle("hamcp191 boundary ordinary").lower()
        for name, arguments in ORDINARY_ARGUMENTS.items():
            with self.subTest(tool=name):
                service = self.service(known)
                rendered = await self.call(name, copy.deepcopy(arguments), service)
                self.assertNotEqual(json.loads(rendered).get("details"), REFUSAL)
                self.assertNotEqual(service.method_calls, [])


# ---------------------------------------------------------------------------
# M2: no orphaned dashboard artifact.


class DashboardArtifactOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.access = syn.needle("hamcp191 m2 access")
        self.dashboard = dashboard_fixture._DashboardGateway()
        self.repository = ChangePlanRepository(root / "plans")
        self.service = ChangeGovernanceService(
            self.repository,
            dashboard_fixture._UnusedConfigurationGateway(),
            AuditLogger(str(root / "audit.jsonl"), self.access),
            dashboard_gateway=self.dashboard,
            provider_identity_reader=dashboard_fixture._unrelated_lifecycle_provider_identity,
            sensitive_values=(self.access,),
        )
        self.service.f3_runtime = F3RuntimeIntegration(
            service=self.service,
            storage_root=str(root / "plans"),
            configuration_gateway=dashboard_fixture._UnusedConfigurationGateway(),
            backup_gateway=None,
            lifecycle_gateway=None,
            dashboard_gateway=self.dashboard,
            provider_identity_reader=dashboard_fixture._unrelated_lifecycle_provider_identity,
            retention_days=90,
        )
        await self.service.f3_runtime.recover_once("startup")
        self.telemetry, self.context = begin_request("hamcp191-m2-request")
        self.telemetry.caller_id = "mcp-requester"

    async def asyncTearDown(self):
        end_request(self.context)
        self.temp.cleanup()

    def artifacts(self):
        return sorted((self.repository.root / "dashboard_artifacts").glob("*.json"))

    async def create(self, url_path="main-operations"):
        return await self.service.create_dashboard_update_plan(
            title="Rename operations dashboard",
            description="Bounded existing-dashboard update.",
            url_path=url_path,
            patch_operations=[
                {"operation_id": "rename", "operation": "replace", "path": "/title", "value": "After"}
            ],
            expiration_minutes=30,
        )

    async def assert_refused_without_artifact(self, call, secret, *, prereads):
        with self.assertRaises(GovernanceError) as raised:
            await call
        self.assertEqual(raised.exception.code, ErrorCode.CONFIGURATION_VALIDATION_FAILED)
        self.assertNotIn(secret, json.dumps(raised.exception.details, default=str))
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.artifacts(), [])
        self.assertNotIn(secret, disk_text(self.repository.root))
        self.assertEqual(self.dashboard.preread_count, prereads)
        return raised.exception

    async def test_secret_caller_id_is_refused_before_any_read_or_artifact(self):
        # Antigravity M2 repro: the artifact held the raw caller ID.
        secret = syn.matter_payload(syn.needle("hamcp191 m2 caller"))
        self.telemetry.caller_id = "caller-" + secret
        error = await self.assert_refused_without_artifact(self.create(), secret, prereads=0)
        self.assertEqual(error.details, {"reason": "dashboard_contains_prohibited_sensitive_data"})

    async def test_secret_url_path_is_refused_before_any_read_or_artifact(self):
        secret = syn.needle("hamcp191 m2 url path").lower()
        self.service.sensitive_values = (self.access, secret)
        error = await self.assert_refused_without_artifact(self.create(url_path=secret), secret, prereads=0)
        self.assertEqual(error.details, {"reason": "dashboard_contains_prohibited_sensitive_data"})

    async def test_a_plan_gate_refusal_after_the_read_leaves_no_artifact(self):
        refusal = GovernanceError(
            ErrorCode.CONFIGURATION_VALIDATION_FAILED,
            details={"reason": "plan_contains_prohibited_sensitive_data"},
        )
        with patch.object(self.service, "_require_strict_new_plan", side_effect=refusal) as gate:
            await self.assert_refused_without_artifact(self.create(), self.access, prereads=1)
        gate.assert_called_once()

    async def test_ordinary_plan_writes_exactly_one_bound_artifact(self):
        created = await self.create()
        self.assertEqual(created["status"], "awaiting_approval")
        artifacts = self.artifacts()
        self.assertEqual([path.stem for path in artifacts], [created["plan_id"]])
        envelope = json.loads(artifacts[0].read_text(encoding="utf-8"))
        stored = self.repository.get(created["plan_id"])
        self.assertIn(envelope["payload_sha256"], json.dumps(stored.to_dict()))
        self.assertEqual(envelope["payload"]["requested_by"], "mcp-requester")


# ---------------------------------------------------------------------------
# L3: the D11 stored-plan re-check runs for contract-v1 records.


class ContractV1StoredPlanTests(_GovernanceHarness):
    async def stored_v1(self, mutate=None):
        created = await self.create()
        (path,) = [p for p in plan_files(self.repository.root) if p.stem == created["plan_id"]]
        record = json.loads(path.read_text(encoding="utf-8"))
        # A contract-v1 record carries no contract_version key on disk.
        self.assertNotIn("contract_version", record)
        self.assertEqual(self.repository.get(created["plan_id"]).contract_version, 1)
        if mutate is not None:
            # A record written before batch 3 (an "old" record): it reaches the
            # disk without this batch's save path.
            mutate(record)
            path.write_text(json.dumps(record), encoding="utf-8")
        return created["plan_id"], path

    async def test_unchanged_secret_in_a_v1_record_is_refused_on_update(self):
        def taint(record):
            record["title"] = "Old " + self.access

        plan_id, path = await self.stored_v1(taint)
        before = path.read_bytes()
        refusals = self.repository.content_refusals
        plan = self.repository.get(plan_id)
        self.assertEqual(plan.contract_version, 1)
        plan.updated_at = "2026-10-19T12:00:00+00:00"
        with self.assertRaises(PlanContentRefused) as raised:
            self.repository.save(plan)
        self.assertFalse(raised.exception.creation)
        self.assertEqual(self.repository.content_refusals - refusals, 1)
        self.assertEqual(path.read_bytes(), before)

    async def test_an_ordinary_v1_record_still_updates(self):
        plan_id, path = await self.stored_v1()
        plan = self.repository.get(plan_id)
        plan.updated_at = "2026-10-19T12:00:00+00:00"
        self.repository.save(plan)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["updated_at"], plan.updated_at)


if __name__ == "__main__":
    unittest.main()
