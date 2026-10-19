"""HAMCP-205: one administrator check for every Ingress approval page.

Absorbs HAMCP-199. Every review and every decision asks Home Assistant, for that
request, whether the forwarded user is an administrator; anything other than a
positive answer refuses with no state change, no device read and no CSRF token.
All transports here are synthetic; no live Home Assistant is contacted.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

import httpx  # noqa: E402

from ha_mcp_engineering import approval_identity  # noqa: E402
from ha_mcp_engineering.approval_identity import (  # noqa: E402
    APPROVAL_IDENTITY_COUNTERS,
    AUTH_LIST_COMMAND,
    AdministratorVerdict,
    AdministratorVerifier,
    administrator_verdict,
    ingress_user_id,
)
from ha_mcp_engineering import approval_web  # noqa: E402
from ha_mcp_engineering.approval_web import (  # noqa: E402
    IngressApprovalApplication,
    create_approval_application,
)
from ha_mcp_engineering.audit import AuditRecord  # noqa: E402
from ha_mcp_engineering.clients.single_read import SINGLE_READ  # noqa: E402
from ha_mcp_engineering.governance.models import ApprovalState  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.governance import service as governance_service  # noqa: E402
from ha_mcp_engineering.health import HealthRegistry  # noqa: E402
from tests.approval_identity_support import StaticAdministratorVerifier  # noqa: E402
from tests.test_beta25_external_approval import (  # noqa: E402
    ExternalApprovalTestCase,
    RuntimeShim,
)
import tests.test_f3_runtime_integration as f3  # noqa: E402
import tests.test_hamcp192_observing_fix as observing  # noqa: E402
from tests.test_hamcp184_principal_separation_honesty import (  # noqa: E402
    plant_anonymous_decision,
)


ADMIN, USER = "admin-hamcp205", "user-hamcp205"
INGRESS_PATH = "/api/hassio_ingress/hamcp205fixture"
PEER = ("172.30.32.2", 12345)
ADMINISTRATOR = AdministratorVerdict.ADMINISTRATOR
NOT_ADMINISTRATOR = AdministratorVerdict.NOT_ADMINISTRATOR
LOOKUP_FAILED = AdministratorVerdict.LOOKUP_FAILED
IDENTITY_MISSING = AdministratorVerdict.IDENTITY_MISSING
TASK_ID = "a" * 32

# Every Ingress approval route: (method, path, audited surface).
ROUTES = (
    ("GET", "/", "plan_inbox"),
    ("GET", "/plans/plan-hamcp205", "plan_review"),
    ("POST", "/plans/plan-hamcp205/approve", "plan_approve"),
    ("POST", "/plans/plan-hamcp205/reject", "plan_reject"),
    ("GET", "/f3", "f3_inbox"),
    ("GET", "/f3/child-hamcp205", "f3_review"),
    ("POST", "/f3/child-hamcp205/reconcile", "f3_reconcile"),
    ("GET", "/typed", "typed_inbox"),
    ("GET", f"/typed/fan/{TASK_ID}", "typed_review"),
    ("POST", f"/typed/fan/{TASK_ID}/release", "typed_release"),
)
REFUSAL_TEXT = {
    IDENTITY_MISSING: ("Approver identity missing", 403),
    NOT_ADMINISTRATOR: ("Administrator required", 403),
    LOOKUP_FAILED: ("Could not verify administrator role", 503),
}


def user_row(user_id=ADMIN, **overrides):
    """One ``config/auth/list`` row as Core 2026.10.x ``_user_info`` returns it."""

    row = {
        "id": user_id,
        "username": user_id,
        "name": user_id,
        "is_owner": False,
        "is_active": True,
        "local_only": False,
        "system_generated": False,
        "group_ids": ["system-admin"],
        "credentials": [{"type": "homeassistant"}],
    }
    row.update(overrides)
    return row


class Tripwire:
    """Any attribute use proves that a refused request reached state."""

    def __init__(self):
        self.touched = []

    def __getattr__(self, name):
        self.touched.append(name)
        raise AssertionError(f"refused request touched {name}")


class Ledger:
    def __init__(self):
        self.records: list[AuditRecord] = []

    def __call__(self, record):
        self.records.append(record)
        return True


class CounterIsolation:
    """The refusal counter is process-wide; give every test a clean count."""

    def isolate_counters(self):
        APPROVAL_IDENTITY_COUNTERS.reset()
        self.addCleanup(APPROVAL_IDENTITY_COUNTERS.reset)

    @staticmethod
    def refusals():
        return APPROVAL_IDENTITY_COUNTERS.snapshot()["refused_by_reason"]


def ingress_client(app, *, user=ADMIN, peer=PEER, extra=None):
    headers = [("X-Ingress-Path", INGRESS_PATH)]
    if user is not None:
        headers.append(("X-Remote-User-Id", user))
    headers.extend(extra or ())
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=peer),
        base_url="http://hamcp205.local",
        headers=headers,
    )


def hidden(html):
    return dict(re.findall(r'name="([a-z_]+)" value="([^"]*)"', html))


class IngressIdentityTests(unittest.TestCase):
    def test_exactly_one_well_formed_value_is_an_identity(self):
        self.assertEqual(ingress_user_id([ADMIN]), ADMIN)
        for values in ([], [""], ["<script>"], ["a b"], ["x" * 129],
                       [ADMIN, ADMIN], [ADMIN, "other"]):
            with self.subTest(values=values):
                self.assertIsNone(ingress_user_id(values))


class AdministratorVerdictTests(unittest.TestCase):
    def verdict(self, *rows, user_id=ADMIN):
        return administrator_verdict(list(rows), user_id)

    def test_core_is_admin_rule(self):
        cases = (
            (user_row(), ADMINISTRATOR),
            (user_row(is_owner=True, group_ids=[]), ADMINISTRATOR),
            (user_row(group_ids=["system-users"]), NOT_ADMINISTRATOR),
            (user_row(group_ids=["system-read-only"]), NOT_ADMINISTRATOR),
            (user_row(is_active=False), NOT_ADMINISTRATOR),
            # L1: an inactive owner is never an Ingress approver, although
            # Core's is_admin still calls it an administrator.
            (user_row(is_owner=True, is_active=False), NOT_ADMINISTRATOR),
            (user_row(is_owner=True, is_active=False, group_ids=[]), NOT_ADMINISTRATOR),
            # Integration/system accounts are never Ingress approvers.
            (user_row(system_generated=True), NOT_ADMINISTRATOR),
            (user_row(system_generated=True, is_owner=True), NOT_ADMINISTRATOR),
        )
        for row, expected in cases:
            with self.subTest(row=row):
                self.assertIs(self.verdict(user_row("someone-else"), row), expected)

    def test_unknown_duplicate_or_malformed_results_fail_closed(self):
        cases = (
            {"users": [user_row()]},
            None,
            [],
            [user_row("someone-else")],
            [user_row(), user_row()],
            [user_row(), "row"],
            [{"name": "no id"}],
            [user_row(is_owner="true")],
            [user_row(is_active=1)],
            [user_row(system_generated=None)],
            [user_row(group_ids="system-admin")],
            [user_row(group_ids=[["system-admin"]])],
            [user_row(f"user-{index}") for index in range(1_001)],
        )
        for result in cases:
            with self.subTest(result=str(result)[:80]):
                self.assertIs(administrator_verdict(result, ADMIN), LOOKUP_FAILED)


class SyntheticWebSocket:
    """Stands in for ``HomeAssistantWebSocketClient.command``."""

    def __init__(self, *results, delay=0.0):
        self.results = list(results)
        self.delay = delay
        self.commands = []
        self.single_read = []

    async def command(self, payload):
        self.commands.append(payload)
        self.single_read.append(SINGLE_READ.get())
        if self.delay:
            await asyncio.sleep(self.delay)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class AdministratorVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_only_the_fixed_list_command_as_a_bounded_read(self):
        socket = SyntheticWebSocket([user_row()])
        self.assertIs(await AdministratorVerifier(socket).verify(ADMIN), ADMINISTRATOR)
        self.assertEqual(socket.commands, [{"type": "config/auth/list"}])
        self.assertEqual(socket.single_read, [True])
        self.assertFalse(SINGLE_READ.get())
        socket.commands[0]["type"] = "config/auth/delete"
        self.assertEqual(dict(AUTH_LIST_COMMAND), {"type": "config/auth/list"})

    async def test_missing_or_malformed_identity_never_looks_up(self):
        socket = SyntheticWebSocket()
        verifier = AdministratorVerifier(socket)
        for user_id in (None, "", "bad id", "x" * 129):
            self.assertIs(await verifier.verify(user_id), IDENTITY_MISSING)
        self.assertEqual(socket.commands, [])

    async def test_every_lookup_failure_refuses(self):
        for result in (RuntimeError("unavailable"), asyncio.TimeoutError(),
                       [user_row("someone-else")], {"type": "result"}):
            with self.subTest(result=repr(result)):
                socket = SyntheticWebSocket(result)
                self.assertIs(await AdministratorVerifier(socket).verify(ADMIN), LOOKUP_FAILED)

    async def test_slow_lookup_times_out(self):
        socket = SyntheticWebSocket([user_row()], delay=1.0)
        verifier = AdministratorVerifier(socket, timeout_seconds=0.01)
        self.assertIs(await verifier.verify(ADMIN), LOOKUP_FAILED)

    async def test_no_result_is_cached_between_requests(self):
        socket = SyntheticWebSocket(
            [user_row()], [user_row(group_ids=["system-users"])], [user_row()]
        )
        verifier = AdministratorVerifier(socket)
        self.assertIs(await verifier.verify(ADMIN), ADMINISTRATOR)
        self.assertIs(await verifier.verify(ADMIN), NOT_ADMINISTRATOR)
        self.assertIs(await verifier.verify(ADMIN), ADMINISTRATOR)
        self.assertEqual(len(socket.commands), 3)


class SharedGateRefusalTests(CounterIsolation, unittest.IsolatedAsyncioTestCase):
    """Every route refuses before any governance, F3 or typed state is used."""

    async def asyncSetUp(self):
        self.isolate_counters()

    def build(self, verifier):
        self.governance, self.typed = Tripwire(), Tripwire()
        self.factories = []
        self.ledger = Ledger()

        def factory():
            self.factories.append("fan")
            return self.typed

        self.application = IngressApprovalApplication(
            self.governance,
            typed_services={"fan": factory},
            administrator_verifier=verifier,
            audit_sink=self.ledger,
        )
        return self.application.create()

    async def send(self, client, method, path):
        if method == "GET":
            return await client.get(path)
        return await client.post(path, data={"csrf": "x", "challenge_id": "y"})

    def assert_untouched(self):
        self.assertEqual(self.governance.touched, [])
        self.assertEqual(self.typed.touched, [])
        self.assertEqual(self.factories, [])
        self.assertEqual(self.application._f3_csrf, {})
        self.assertEqual(self.application._typed_csrf, {})

    async def assert_refused_everywhere(self, verdict, *, user=ADMIN, verifier, extra=None,
                                        recorded_caller=None):
        app = self.build(verifier)
        title, status = REFUSAL_TEXT[verdict]
        async with ingress_client(app, user=user, extra=extra) as client:
            for method, path, surface in ROUTES:
                with self.subTest(verdict=verdict.value, surface=surface):
                    before = len(self.ledger.records)
                    response = await self.send(client, method, path)
                    self.assertEqual(response.status_code, status)
                    self.assertIn(title, response.text)
                    self.assertIn("Nothing was shown or changed", response.text)
                    self.assertNotIn('name="csrf"', response.text)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertEqual(len(self.ledger.records), before + 1)
                    record = self.ledger.records[-1]
                    self.assertEqual(record.event, "ingress_approval_identity_refused")
                    self.assertEqual(record.operation_category, surface)
                    self.assertEqual(record.parameters, {"surface": surface, "method": method})
                    self.assertEqual(record.result_status, "refused")
                    self.assertEqual(record.error_code, verdict.value)
                    self.assertEqual(record.caller_id, recorded_caller)
                    serialized = json.dumps(record.as_dict())
                    self.assertNotIn(INGRESS_PATH, serialized)
                    self.assertNotIn("<script>", serialized)
        self.assert_untouched()
        self.assertEqual(self.refusals()[verdict.value], len(ROUTES))

    async def test_non_administrator_is_refused_on_every_page(self):
        verifier = StaticAdministratorVerifier({USER: NOT_ADMINISTRATOR})
        await self.assert_refused_everywhere(
            NOT_ADMINISTRATOR, user=USER, verifier=verifier,
            recorded_caller=f"home_assistant_admin_ingress:{USER}",
        )
        self.assertEqual(verifier.calls, [USER] * len(ROUTES))

    async def test_missing_identity_is_refused_without_a_lookup(self):
        # HAMCP-199: no X-Remote-User-Id is no longer an anonymous approver.
        verifier = StaticAdministratorVerifier()
        await self.assert_refused_everywhere(IDENTITY_MISSING, user=None, verifier=verifier)
        self.assertEqual(verifier.calls, [])

    async def test_malformed_identity_is_refused_and_never_recorded(self):
        verifier = StaticAdministratorVerifier()
        for user in ("", "<script>x</script>", "a b", "x" * 129):
            with self.subTest(user=user):
                APPROVAL_IDENTITY_COUNTERS.reset()
                await self.assert_refused_everywhere(IDENTITY_MISSING, user=user, verifier=verifier)
        self.assertEqual(verifier.calls, [])

    async def test_repeated_identity_header_is_refused(self):
        # Supervisor writes one value; a second can only come from the client.
        verifier = StaticAdministratorVerifier()
        for second in (ADMIN, "forged-admin"):
            with self.subTest(second=second):
                APPROVAL_IDENTITY_COUNTERS.reset()
                await self.assert_refused_everywhere(
                    IDENTITY_MISSING, verifier=verifier,
                    extra=[("x-remote-user-id", second)],
                )
        self.assertEqual(verifier.calls, [])

    async def test_lookup_failure_is_refused(self):
        await self.assert_refused_everywhere(
            LOOKUP_FAILED, verifier=StaticAdministratorVerifier(default=LOOKUP_FAILED),
            recorded_caller=f"home_assistant_admin_ingress:{ADMIN}",
        )

    async def test_verifier_exception_or_unknown_verdict_is_a_lookup_failure(self):
        class Raising:
            async def verify(self, user_id):
                raise RuntimeError("transport down")

        class Unknown:
            async def verify(self, user_id):
                return "administrator"  # a string, not the verdict

        for verifier in (Raising(), Unknown()):
            with self.subTest(verifier=type(verifier).__name__):
                APPROVAL_IDENTITY_COUNTERS.reset()
                await self.assert_refused_everywhere(
                    LOOKUP_FAILED, verifier=verifier,
                    recorded_caller=f"home_assistant_admin_ingress:{ADMIN}",
                )

    async def test_unconfigured_verifier_fails_closed(self):
        await self.assert_refused_everywhere(
            LOOKUP_FAILED, verifier=None,
            recorded_caller=f"home_assistant_admin_ingress:{ADMIN}",
        )

    async def test_non_ingress_peer_or_path_is_refused_before_identity(self):
        verifier = StaticAdministratorVerifier()
        app = self.build(verifier)
        for peer, path_header in ((("127.0.0.1", 1), INGRESS_PATH), (PEER, ""), (PEER, "/elsewhere")):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=peer),
                base_url="http://hamcp205.local",
                headers={"X-Ingress-Path": path_header, "X-Remote-User-Id": ADMIN},
            ) as client:
                for method, path, surface in ROUTES:
                    with self.subTest(peer=peer[0], header=path_header, surface=surface):
                        response = await self.send(client, method, path)
                        self.assertEqual(response.status_code, 403)
                        self.assertIn("only through Home Assistant Ingress", response.text)
        self.assertEqual(verifier.calls, [])
        self.assert_untouched()

    async def test_refusal_stands_when_the_audit_write_fails(self):
        def failing(record):
            raise OSError("disk full")

        app = IngressApprovalApplication(
            Tripwire(), administrator_verifier=StaticAdministratorVerifier(default=NOT_ADMINISTRATOR),
            audit_sink=failing,
        ).create()
        async with ingress_client(app) as client:
            response = await client.get("/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.refusals()["not_administrator"], 1)

    async def test_default_audit_sink_is_the_governance_audit_logger(self):
        ledger = Ledger()

        class Governance:
            def require(self):
                return type("Service", (), {"audit": type("Audit", (), {"write": staticmethod(ledger)})()})()

        app = create_approval_application(
            Governance(), administrator_verifier=StaticAdministratorVerifier(default=NOT_ADMINISTRATOR),
        )
        async with ingress_client(app) as client:
            self.assertEqual((await client.get("/typed")).status_code, 403)
        self.assertEqual([record.operation_category for record in ledger.records], ["typed_inbox"])

    async def test_health_reports_refusals_by_reason(self):
        verifier = StaticAdministratorVerifier({USER: NOT_ADMINISTRATOR, "flaky": LOOKUP_FAILED})
        app = self.build(verifier)
        for user in (USER, USER, "flaky", None):
            async with ingress_client(app, user=user) as client:
                await client.get("/")
        health = HealthRegistry().snapshot({"checked": False, "status": "not_checked"})
        self.assertEqual(health["approval_identity"], {
            "administrator_role_source": "home_assistant_core_config_auth_list_per_request",
            "refused_attempts": 4,
            "refused_by_reason": {"identity_missing": 1, "not_administrator": 2, "lookup_failed": 1},
            "last_refusal_reason": "identity_missing",
            "counter_semantics": "cumulative_process_events",
        })


class NoAnonymousApproverSourceTests(unittest.TestCase):
    def test_ingress_application_has_no_default_principal(self):
        source = Path(approval_web.__file__).read_text(encoding="utf-8")
        self.assertNotIn("DEFAULT_APPROVER_PRINCIPAL", source)
        self.assertFalse(hasattr(approval_web, "DEFAULT_APPROVER_PRINCIPAL"))
        # The principal is built only from a verified identity.
        self.assertEqual(source.count("administrator_principal(user_id)"), 2)

    def test_production_wiring_installs_the_real_verifier(self):
        from ha_mcp_engineering import application

        with patch.object(application, "HomeAssistantWebSocketClient") as client:
            app = application.create_approval_application(object())
        self.assertIsNotNone(app)
        client.assert_called_once()
        source = Path(application.__file__).read_text(encoding="utf-8")
        self.assertIn("AdministratorVerifier(", source)

    def test_verifier_module_reads_through_one_fixed_command(self):
        source = Path(approval_identity.__file__).read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'"type": "([^"]+)"', source), ["config/auth/list"])
        self.assertEqual(source.count(".command("), 1)


class PlanApprovalAdministratorTests(CounterIsolation, ExternalApprovalTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.isolate_counters()
        self.verifier = StaticAdministratorVerifier({USER: NOT_ADMINISTRATOR})
        self.app = create_approval_application(
            RuntimeShim(self.service), administrator_verifier=self.verifier,
        )

    def client(self, user=ADMIN):
        return ingress_client(self.app, user=user)

    async def pending(self):
        created = await self.create()
        self.service.approve(created["plan_id"], created["plan_hash"])
        return created

    async def test_administrator_reviews_and_approves_with_verified_principal(self):
        created = await self.pending()
        async with self.client() as client:
            self.assertIn(created["plan_id"], (await client.get("/")).text)
            review = await client.get(f"/plans/{created['plan_id']}")
            self.assertEqual(review.status_code, 200)
            approved = await client.post(f"/plans/{created['plan_id']}/approve", data=hidden(review.text))
            self.assertEqual(approved.status_code, 200, approved.text)
        approval = self.repository.get(created["plan_id"]).approval
        self.assertEqual(approval.state, ApprovalState.APPROVED)
        self.assertEqual(approval.approver_principal, f"home_assistant_admin_ingress:{ADMIN}")
        self.assertTrue(approval.principal_separation_enforced)
        # One lookup per request: inbox, review and decision.
        self.assertEqual(self.verifier.calls, [ADMIN] * 3)
        self.assertEqual(self.refusals(), {"identity_missing": 0, "not_administrator": 0, "lookup_failed": 0})

    async def test_administrator_rejects_with_verified_principal(self):
        created = await self.pending()
        async with self.client() as client:
            review = await client.get(f"/plans/{created['plan_id']}")
            rejected = await client.post(f"/plans/{created['plan_id']}/reject", data=hidden(review.text))
            self.assertEqual(rejected.status_code, 200, rejected.text)
        approval = self.repository.get(created["plan_id"]).approval
        self.assertEqual(approval.state, ApprovalState.REJECTED)
        self.assertEqual(approval.approver_principal, f"home_assistant_admin_ingress:{ADMIN}")

    async def test_non_administrator_review_issues_no_csrf(self):
        created = await self.pending()
        with patch.object(self.service, "issue_external_csrf", wraps=self.service.issue_external_csrf) as issue:
            async with self.client(USER) as client:
                self.assertEqual((await client.get("/")).status_code, 403)
                review = await client.get(f"/plans/{created['plan_id']}")
            self.assertEqual(review.status_code, 403)
            issue.assert_not_called()
        approval = self.repository.get(created["plan_id"]).approval
        self.assertEqual(approval.state, ApprovalState.EXTERNAL_PENDING)
        self.assertIsNone(approval.csrf_digest)

    async def test_role_revoked_between_review_and_decision_is_refused(self):
        created = await self.pending()
        async with self.client() as client:
            review = await client.get(f"/plans/{created['plan_id']}")
            form = hidden(review.text)
            for decision, revoked, expected in (
                ("approve", NOT_ADMINISTRATOR, 403),
                ("reject", NOT_ADMINISTRATOR, 403),
                ("approve", LOOKUP_FAILED, 503),
            ):
                with self.subTest(decision=decision, revoked=revoked.value):
                    self.verifier.roles[ADMIN] = revoked
                    response = await client.post(f"/plans/{created['plan_id']}/{decision}", data=form)
                    self.assertEqual(response.status_code, expected)
                    self.assertEqual(
                        self.repository.get(created["plan_id"]).approval.state,
                        ApprovalState.EXTERNAL_PENDING,
                    )
            # The refusals consumed nothing: the restored administrator's
            # untouched form still decides exactly once.
            self.verifier.roles[ADMIN] = ADMINISTRATOR
            approved = await client.post(f"/plans/{created['plan_id']}/approve", data=form)
            self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(self.refusals(), {"identity_missing": 0, "not_administrator": 2, "lookup_failed": 1})

    async def test_missing_identity_cannot_decide(self):
        created = await self.pending()
        async with self.client() as client:
            form = hidden((await client.get(f"/plans/{created['plan_id']}")).text)
        async with self.client(None) as anonymous:
            response = await anonymous.post(f"/plans/{created['plan_id']}/approve", data=form)
        self.assertEqual(response.status_code, 403)
        self.assertIn("Approver identity missing", response.text)
        self.assertEqual(self.repository.get(created["plan_id"]).approval.state, ApprovalState.EXTERNAL_PENDING)

    async def test_refusal_is_one_audit_entry_without_secrets(self):
        await self.pending()
        async with self.client(USER) as client:
            await client.get("/")
        entries = [json.loads(line) for line in self.audit_path.read_text().splitlines()]
        refused = [entry for entry in entries if entry["event"] == "ingress_approval_identity_refused"]
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["caller_id"], f"home_assistant_admin_ingress:{USER}")
        self.assertEqual(refused[0]["error_code"], "not_administrator")
        text = json.dumps(refused[0])
        self.assertNotIn(INGRESS_PATH, text)
        self.assertNotIn("beta25-test-access-secret", text)


class F3ReconciliationAdministratorTests(CounterIsolation, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.isolate_counters()
        self.runtime = f3._IngressRuntimeStub()
        self.verifier = StaticAdministratorVerifier({USER: NOT_ADMINISTRATOR})
        self.ledger = Ledger()
        self.app = create_approval_application(
            f3._IngressGovernanceStub(self.runtime),
            administrator_verifier=self.verifier, audit_sink=self.ledger,
        )

    async def test_administrator_reviews_and_reconciles(self):
        async with ingress_client(self.app) as client:
            self.assertIn("child-f3-ingress", (await client.get("/f3")).text)
            review = await client.get("/f3/child-f3-ingress")
            self.assertEqual(review.status_code, 200)
            form = {**hidden(review.text), "action": "retain_hold"}
            done = await client.post("/f3/child-f3-ingress/reconcile", data=form)
            self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(self.runtime.calls[0]["authorized_principal"], f"home_assistant_admin_ingress:{ADMIN}")
        self.assertEqual(self.verifier.calls, [ADMIN] * 3)

    async def test_non_administrator_sees_and_changes_nothing(self):
        async with ingress_client(self.app, user=USER) as client:
            for method, path in (("GET", "/f3"), ("GET", "/f3/child-f3-ingress"),
                                 ("POST", "/f3/child-f3-ingress/reconcile")):
                response = await (client.get(path) if method == "GET" else
                                  client.post(path, data={"action": "retain_hold"}))
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("child-f3-ingress", response.text)
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(self.refusals()["not_administrator"], 3)

    async def test_role_revoked_between_review_and_reconcile_is_refused(self):
        async with ingress_client(self.app) as client:
            review = await client.get("/f3/child-f3-ingress")
            form = {**hidden(review.text), "action": "retain_hold"}
            self.verifier.roles[ADMIN] = NOT_ADMINISTRATOR
            refused = await client.post("/f3/child-f3-ingress/reconcile", data=form)
            self.assertEqual(refused.status_code, 403)
            self.assertEqual(self.runtime.calls, [])
            self.verifier.roles[ADMIN] = ADMINISTRATOR
            done = await client.post("/f3/child-f3-ingress/reconcile", data=form)
            self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(len(self.runtime.calls), 1)


class TypedHoldReleaseAdministratorTests(CounterIsolation, observing.ObservingFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.isolate_counters()
        self.verifier = StaticAdministratorVerifier({USER: NOT_ADMINISTRATOR})
        self.app = create_approval_application(
            object(), typed_services={"fan": lambda: self.service},
            administrator_verifier=self.verifier, audit_sink=Ledger(),
        )

    def held(self, task_id):
        return [item.conflict_hold for item in self.owned(task_id)]

    async def test_administrator_reviews_and_releases(self):
        request = await self.held_task()
        path = f"/typed/fan/{request.task_id}"
        async with ingress_client(self.app) as client:
            self.assertIn(request.task_id, (await client.get("/typed")).text)
            review = await client.get(path)
            self.assertEqual(review.status_code, 200)
            released = await client.post(path + "/release", data=hidden(review.text))
            self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(self.owned(request.task_id), [])
        self.assertEqual(self.verifier.calls, [ADMIN] * 3)

    async def test_non_administrator_causes_no_device_read_and_keeps_the_hold(self):
        request = await self.held_task()
        path = f"/typed/fan/{request.task_id}"
        reads, writes = self.transport.reads(), self.transport.service_calls()
        async with ingress_client(self.app, user=USER) as client:
            for response in (await client.get("/typed"), await client.get(path),
                             await client.post(path + "/release", data={"csrf": "x"})):
                self.assertEqual(response.status_code, 403)
                self.assertNotIn(request.task_id, response.text)
        self.assertEqual(self.transport.reads(), reads)
        self.assertEqual(self.transport.service_calls(), writes)
        self.assertEqual(self.held(request.task_id), [True])

    async def test_role_revoked_between_review_and_release_is_refused(self):
        request = await self.held_task()
        path = f"/typed/fan/{request.task_id}"
        async with ingress_client(self.app) as client:
            form = hidden((await client.get(path)).text)
            reads = self.transport.reads()
            self.verifier.roles[ADMIN] = NOT_ADMINISTRATOR
            refused = await client.post(path + "/release", data=form)
            self.assertEqual(refused.status_code, 403)
            self.assertEqual(self.transport.reads(), reads)
            self.assertEqual(self.held(request.task_id), [True])
            self.verifier.roles[ADMIN] = LOOKUP_FAILED
            self.assertEqual((await client.post(path + "/release", data=form)).status_code, 503)
            self.assertEqual(self.held(request.task_id), [True])
        self.assertEqual(self.refusals(), {"identity_missing": 0, "not_administrator": 1, "lookup_failed": 1})


class AnonymousApproverRemovalTests(ExternalApprovalTestCase):
    """Owner cleanup: no anonymous default approver, and no stored anonymous
    approval authorizes dispatch; the plan must be re-approved."""

    IDENTIFIED = "home_assistant_admin_ingress:user-1"

    def audit_events(self):
        return [json.loads(line) for line in self.audit_path.read_text().splitlines()]

    def assert_only_refusal_recorded(self, plan_id, before, event, error_code):
        """The stored plan changed only by one appended refusal event."""

        after = self.repository.get(plan_id).to_dict()
        added = after["events"][len(before["events"]):]
        self.assertEqual(after["events"][:len(before["events"])], before["events"])
        self.assertEqual(
            [(item["event"], item.get("error_code")) for item in added],
            [(event, error_code)],
        )
        self.assertEqual(
            {k: v for k, v in after.items() if k != "events"},
            {k: v for k, v in before.items() if k != "events"},
        )

    async def approved_then_planted(self, *, recorded_flag, description="After"):
        created = await self.create(description=description)
        await self.grant(created, principal=self.IDENTIFIED)
        plant_anonymous_decision(
            self.repository, created["plan_id"], recorded_flag=recorded_flag
        )
        return created

    def test_governance_has_no_default_principal(self):
        source = Path(governance_service.__file__).read_text(encoding="utf-8")
        self.assertNotIn("DEFAULT_APPROVER_PRINCIPAL", source)
        self.assertFalse(hasattr(governance_service, "DEFAULT_APPROVER_PRINCIPAL"))
        # The removed fallback was ``(approver_principal or DEFAULT_...)``.
        self.assertNotRegex(source, r"\(\s*approver_principal\s+or\s")

    async def test_stored_anonymous_approval_is_refused_at_apply_with_reapprove_reason(self):
        for recorded_flag in (False, True):
            with self.subTest(recorded_flag=recorded_flag):
                created = await self.approved_then_planted(
                    recorded_flag=recorded_flag, description=f"After {recorded_flag}"
                )
                self.assertIs(
                    self.service.get_plan(created["plan_id"])["apply_allowed"], False
                )
                before = self.repository.get(created["plan_id"]).to_dict()
                events = len(self.audit_events())
                reads, writes = self.gateway.reads, self.gateway.writes
                with self.assertRaises(GovernanceError) as raised:
                    await self.service.apply(created["plan_id"], created["plan_hash"])
                self.assertEqual(raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_REQUIRED)
                self.assertEqual(
                    raised.exception.details,
                    {"reason": "anonymous_approval_requires_reapproval"},
                )
                # No dispatch, no read, no state change beyond the refusal record.
                self.assertEqual((self.gateway.reads, self.gateway.writes), (reads, writes))
                self.assert_only_refusal_recorded(
                    created["plan_id"], before,
                    "change_apply_rejected", "external_approval_required",
                )
                self.assertIsNone(
                    self.service.task_repository.get_for_plan(created["plan_id"])
                )
                added = self.audit_events()[events:]
                self.assertEqual(
                    [(r.get("event"), r.get("error_code")) for r in added],
                    [("change_apply_rejected", "external_approval_required")],
                )

    async def test_stored_anonymous_rollback_approval_is_refused_with_reapprove_reason(self):
        created = await self.create()
        await self.grant(created, principal=self.IDENTIFIED)
        await self.service.apply(created["plan_id"], created["plan_hash"])
        rollback = await self.service.rollback_change(created["plan_id"])
        rollback_created = {"plan_id": created["plan_id"], "plan_hash": rollback["plan_hash"]}
        await self.grant(rollback_created, kind="rollback", principal=self.IDENTIFIED)
        plant_anonymous_decision(self.repository, created["plan_id"], recorded_flag=False)
        before = self.repository.get(created["plan_id"]).to_dict()
        writes = self.gateway.writes
        with self.assertRaises(GovernanceError) as raised:
            await self.service.rollback_change(created["plan_id"], rollback["plan_hash"])
        self.assertEqual(raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_REQUIRED)
        self.assertEqual(
            raised.exception.details["reason"], "anonymous_approval_requires_reapproval"
        )
        self.assertEqual(self.gateway.writes, writes)
        self.assert_only_refusal_recorded(
            created["plan_id"], before, "rollback_failed", "external_approval_required"
        )

    async def test_reapproval_by_a_verified_administrator_applies(self):
        stale = await self.approved_then_planted(recorded_flag=False)
        with self.assertRaises(GovernanceError):
            await self.service.apply(stale["plan_id"], stale["plan_hash"])
        # Re-approval: the same change planned again and approved by a
        # verified administrator. The stored anonymous plan is superseded.
        fresh = await self.create()
        await self.grant(fresh, principal=self.IDENTIFIED)
        applied = await self.service.apply(fresh["plan_id"], fresh["plan_hash"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(self.gateway.writes, 1)
        record = self.repository.get(fresh["plan_id"])
        self.assertEqual(record.approval.approver_principal, self.IDENTIFIED)
        self.assertIs(record.approval.principal_separation_enforced, True)
        self.assertNotEqual(
            self.repository.get(stale["plan_id"]).status.value, "applied"
        )

    async def test_service_refuses_a_missing_or_anonymous_principal(self):
        for principal in ("", None, "home_assistant_admin_ingress"):
            with self.subTest(principal=principal):
                created = await self.create(description=f"After {principal!r}")
                with self.assertRaises(GovernanceError) as raised:
                    await self.grant(created, principal=principal)
                self.assertEqual(raised.exception.code, ErrorCode.EXTERNAL_APPROVAL_INVALID)
                record = self.repository.get(created["plan_id"])
                self.assertEqual(record.approval.state, ApprovalState.EXTERNAL_PENDING)
                self.assertIsNone(record.approval.approver_principal)
        self.assertEqual(self.gateway.writes, 0)


if __name__ == "__main__":
    unittest.main()
