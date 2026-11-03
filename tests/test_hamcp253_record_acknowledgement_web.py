"""HAMCP-253: the Ingress record pages are the only acknowledgement surface.

Every route checks that the forwarded user is an administrator for this
request. A review page offers the decision form only for an acknowledgeable
record and binds a single-use CSRF token to that record, that administrator
and the exact fingerprint the page displayed. A decision appends to the
acknowledgement ledger only. It never writes the record or calls Home
Assistant.

All transports are synthetic ASGI clients. No live Home Assistant is
contacted, and the governance fixtures come from the real code paths named in
``tests/test_hamcp253_record_acknowledgement.py``.
"""

from __future__ import annotations

import copy
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

import httpx  # noqa: E402

from ha_mcp_engineering.approval_identity import AdministratorVerdict  # noqa: E402
from ha_mcp_engineering.approval_web import IngressApprovalApplication  # noqa: E402
from tests.approval_identity_support import StaticAdministratorVerifier  # noqa: E402
from tests.test_hamcp253_record_acknowledgement import (  # noqa: E402
    PROJECTION_WARNING,
    Hamcp253Case,
    no_home_assistant,
)


ADMIN_USER = "admin-hamcp253"
NON_ADMIN_USER = "user-hamcp253"
ADMIN_PRINCIPAL = f"home_assistant_admin_ingress:{ADMIN_USER}"
INGRESS_PATH = "/api/hassio_ingress/hamcp253fixture"
PEER = ("172.30.32.2", 12345)
INVALID = "request_invalid_or_stale"
REASON = "Reviewed in the Ingress panel for HAMCP-253"
MARKUP = "<script>alert('hamcp253')</script>"


class RuntimeShim:
    """Copied from tests/test_beta25_external_approval.py: a ready runtime."""

    def __init__(self, service):
        self.service = service

    def require(self):
        return self.service


def ingress_client(app, *, user=ADMIN_USER, peer=PEER):
    # Copied from tests/test_hamcp205_admin_verification.py ingress_client.
    headers = [("X-Ingress-Path", INGRESS_PATH)]
    if user is not None:
        headers.append(("X-Remote-User-Id", user))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=peer),
        base_url="http://hamcp253.local",
        headers=headers,
    )


def hidden(html):
    # Copied from tests/test_hamcp205_admin_verification.py hidden.
    return dict(re.findall(r'name="([a-z_]+)" value="([^"]*)"', html))


class Hamcp253IngressCase(Hamcp253Case):
    def mount(self):
        """Serve the current service; call after any fixture that restarts."""

        self.verifier = StaticAdministratorVerifier(
            {NON_ADMIN_USER: AdministratorVerdict.NOT_ADMINISTRATOR}
        )
        self.application = IngressApprovalApplication(
            RuntimeShim(self.service), administrator_verifier=self.verifier
        )
        return self.application.create()

    @staticmethod
    def review_path(record_id, kind="change_plan"):
        return f"/records/{kind}/{record_id}"

    async def review(self, client, record_id, kind="change_plan"):
        response = await client.get(self.review_path(record_id, kind))
        self.assertEqual(response.status_code, 200)
        return hidden(response.text)

    async def decide(self, client, record_id, fields, kind="change_plan"):
        return await client.post(self.review_path(record_id, kind) + "/acknowledge", data=fields)

    @staticmethod
    def form(fields, **overrides):
        values = {
            "csrf": fields.get("csrf", ""),
            "record_fingerprint": fields.get("record_fingerprint", ""),
            "action": "acknowledge",
            "classification": "historical_record",
            "reason": REASON,
        }
        values.update(overrides)
        return {name: value for name, value in values.items() if value is not None}

    def assert_invalid_request(self, response, audits_before):
        self.assertEqual(response.status_code, 400)
        self.assertIn("<h1>Invalid request</h1>", response.text)
        audits = self.acknowledgement_audits()
        self.assertEqual(len(audits), audits_before + 1)
        self.assertEqual(audits[-1]["result_status"], "refused")
        self.assertEqual(audits[-1]["error_code"], INVALID)
        self.assertEqual(audits[-1]["caller_id"], ADMIN_PRINCIPAL)
        self.assertEqual(self.ledger_events(), [])


class IngressIdentityTests(Hamcp253IngressCase):
    async def test_non_administrator_and_missing_identity_are_refused(self):
        (plan_id,) = await self.projection_failed_plans()
        before = self.plan_bytes(plan_id)
        app = self.mount()
        refusals = (
            (NON_ADMIN_USER, "Administrator required"),
            (None, "Approver identity missing"),
        )
        for user, title in refusals:
            async with ingress_client(app, user=user) as client:
                with self.subTest(user=user):
                    with no_home_assistant(self):
                        responses = (
                            await client.get("/records"),
                            await client.get(self.review_path(plan_id)),
                            await self.decide(client, plan_id, self.form(
                                {"csrf": "SYNTHETIC-csrf-hamcp253",
                                 "record_fingerprint": "0" * 64})),
                        )
                    for response in responses:
                        self.assertEqual(response.status_code, 403)
                        self.assertIn(f"<h1>{title}</h1>", response.text)
                        self.assertNotIn("csrf", hidden(response.text))
                        self.assertNotIn(plan_id, response.text)
        self.assertEqual(self.application._record_csrf, {})
        self.assertEqual(self.ledger_events(), [])
        self.assertEqual(self.acknowledgement_audits(), [])
        identity = [entry for entry in self.audits()
                    if entry.get("event") == "ingress_approval_identity_refused"]
        self.assertEqual(len(identity), 6)
        self.assertEqual(
            sorted({entry["operation_category"] for entry in identity}),
            ["record_acknowledge", "record_inbox", "record_review"],
        )
        self.assertEqual(self.plan_bytes(plan_id), before)

    async def test_request_outside_ingress_is_refused(self):
        (plan_id,) = await self.projection_failed_plans()
        app = self.mount()
        async with ingress_client(app, peer=("198.51.100.7", 12345)) as client:
            response = await client.get(self.review_path(plan_id))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("csrf", hidden(response.text))
        self.assertEqual(self.application._record_csrf, {})
        self.assertEqual(self.verifier.calls, [])


class IngressReviewPageTests(Hamcp253IngressCase):
    async def test_form_is_offered_only_for_acknowledgeable_records(self):
        (plan_id,) = await self.projection_failed_plans()
        healthy = (await self.update_plan())["plan_id"]
        view = self.inspect_record("change_plan", plan_id)
        app = self.mount()
        async with ingress_client(app) as client:
            response = await client.get(self.review_path(plan_id))
            self.assertEqual(response.status_code, 200)
            self.assertIn("<h1>Governance record</h1>", response.text)
            fields = hidden(response.text)
            self.assertEqual(fields["record_fingerprint"], view["record_fingerprint"])
            self.assertGreaterEqual(len(fields["csrf"]), 32)
            self.assertIn(f'action="{INGRESS_PATH}/records/change_plan/{plan_id}/acknowledge"',
                          response.text)
            self.assertIn((ADMIN_PRINCIPAL, "change_plan", plan_id), self.application._record_csrf)
            # Only the token hash is kept server side.
            binding = self.application._record_csrf[(ADMIN_PRINCIPAL, "change_plan", plan_id)]
            self.assertNotIn(fields["csrf"], binding)
            self.assertEqual(binding[1:], (ADMIN_PRINCIPAL, view["record_fingerprint"]))

            response = await client.get(self.review_path(healthy))
            self.assertEqual(response.status_code, 200)
            self.assertIn("This record cannot be acknowledged.", response.text)
            self.assertNotIn("<form", response.text)
            self.assertEqual(hidden(response.text), {})
            self.assertNotIn((ADMIN_PRINCIPAL, "change_plan", healthy), self.application._record_csrf)

            for path in (self.review_path("0" * 32),
                         self.review_path(plan_id, kind="execution_task"),
                         self.review_path(plan_id, kind="automation"),
                         self.review_path(plan_id.upper())):
                with self.subTest(path=path):
                    response = await client.get(path)
                    self.assertEqual(response.status_code, 404)
                    self.assertIn("<h1>Record unavailable</h1>", response.text)
                    self.assertNotIn("<form", response.text)
        self.assertEqual(len(self.application._record_csrf), 1)
        self.assertEqual(self.ledger_events(), [])

    async def test_record_values_are_escaped(self):
        (plan_id,) = await self.projection_failed_plans()
        view = self.inspect_record("change_plan", plan_id)
        # A view whose every rendered value carries markup; the real view
        # never contains free text, so this proves the renderer, not storage.
        hostile = copy.deepcopy(view)
        for key in ("record_type", "status", "created_at", "updated_at", "policy_class"):
            hostile[key] = MARKUP
        hostile["failure"] = {**view["failure"], "reason_code": MARKUP, "category": MARKUP}
        hostile["unreadable_fields"] = [MARKUP]
        hostile["acknowledgement"] = {**view["acknowledgement"], "state": MARKUP}
        hostile["events"] = [{"order": 1, "event": MARKUP, "result_status": MARKUP}]
        hostile["record_fingerprint"] = '"><script>alert(1)</script>'
        app = self.mount()
        with patch.object(self.service, "inspect_governance_record", return_value=hostile):
            async with ingress_client(app) as client:
                response = await client.get(self.review_path(plan_id))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>", response.text)
        self.assertNotIn('"><script>', response.text)
        self.assertIn("&lt;script&gt;", response.text)


class IngressDecisionTests(Hamcp253IngressCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        (self.plan_id,) = await self.projection_failed_plans()
        self.plan_before = self.plan_bytes(self.plan_id)
        self.app = self.mount()

    async def test_missing_csrf_is_refused_and_audited(self):
        async with ingress_client(self.app) as client:
            fields = await self.review(client, self.plan_id)
            audits = len(self.acknowledgement_audits())
            with no_home_assistant(self):
                response = await self.decide(client, self.plan_id, self.form(fields, csrf=None))
        self.assert_invalid_request(response, audits)
        self.assertEqual(self.plan_bytes(self.plan_id), self.plan_before)

    async def test_wrong_csrf_is_refused_and_consumes_the_binding(self):
        async with ingress_client(self.app) as client:
            fields = await self.review(client, self.plan_id)
            audits = len(self.acknowledgement_audits())
            response = await self.decide(
                client, self.plan_id, self.form(fields, csrf="SYNTHETIC-wrong-csrf-hamcp253")
            )
            self.assert_invalid_request(response, audits)
            # The binding was spent by the failed attempt.
            response = await self.decide(client, self.plan_id, self.form(fields))
            self.assert_invalid_request(response, audits + 1)
        self.assertEqual(self.application._record_csrf, {})

    async def test_replayed_csrf_is_refused(self):
        async with ingress_client(self.app) as client:
            fields = await self.review(client, self.plan_id)
            response = await self.decide(client, self.plan_id, self.form(fields))
            self.assertEqual(response.status_code, 200)
            audits = len(self.acknowledgement_audits())
            replay = await self.decide(client, self.plan_id, self.form(fields))
        self.assertEqual(replay.status_code, 400)
        self.assertIn("<h1>Invalid request</h1>", replay.text)
        recorded = self.acknowledgement_audits()
        self.assertEqual(len(recorded), audits + 1)
        self.assertEqual(recorded[-1]["error_code"], INVALID)
        self.assertEqual(len(self.ledger_events()), 1)

    async def test_csrf_bound_to_another_administrator_is_refused(self):
        async with ingress_client(self.app) as client:
            fields = await self.review(client, self.plan_id)
        audits = len(self.acknowledgement_audits())
        async with ingress_client(self.app, user="other-admin-hamcp253") as other:
            response = await self.decide(other, self.plan_id, self.form(fields))
        self.assertEqual(response.status_code, 400)
        recorded = self.acknowledgement_audits()
        self.assertEqual(len(recorded), audits + 1)
        self.assertEqual(recorded[-1]["error_code"], INVALID)
        self.assertEqual(recorded[-1]["caller_id"],
                         "home_assistant_admin_ingress:other-admin-hamcp253")
        self.assertEqual(self.ledger_events(), [])

    async def test_two_administrators_keep_separate_bindings(self):
        # Regression: a second administrator opening the same record must not
        # replace the first administrator's one-shot binding.
        other_user = "other-admin-hamcp253"
        async with ingress_client(self.app) as first, \
                ingress_client(self.app, user=other_user) as second:
            first_fields = await self.review(first, self.plan_id)
            second_fields = await self.review(second, self.plan_id)
            self.assertEqual(len(self.application._record_csrf), 2)
            with no_home_assistant(self):
                response = await self.decide(first, self.plan_id, self.form(first_fields))
            self.assertEqual(response.status_code, 200)
            self.assertIn(
                ("home_assistant_admin_ingress:" + other_user, "change_plan", self.plan_id),
                self.application._record_csrf,
            )
            with no_home_assistant(self):
                response = await self.decide(
                    second, self.plan_id,
                    self.form(second_fields, classification="current_defect"),
                )
            self.assertEqual(response.status_code, 200)
        events = self.ledger_events()
        self.assertEqual(
            [event["body"]["principal"] for event in events],
            [ADMIN_PRINCIPAL, "home_assistant_admin_ingress:" + other_user],
        )
        self.assertEqual(self.application._record_csrf, {})
        self.assertEqual(self.plan_bytes(self.plan_id), self.plan_before)

    async def test_unavailable_governance_is_a_bounded_503(self):
        async with ingress_client(self.app) as client:
            fields = await self.review(client, self.plan_id)
            audits = len(self.acknowledgement_audits())

            def unavailable():
                raise RuntimeError("SYNTHETIC governance unavailable")

            with patch.object(self.application.governance, "require", unavailable):
                try:
                    response = await self.decide(client, self.plan_id, self.form(fields))
                except RuntimeError as exc:  # the route must answer, not raise
                    self.fail(f"route raised {type(exc).__name__}")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Nothing was recorded", response.text)
        self.assertNotIn("SYNTHETIC", response.text)
        self.assertEqual(len(self.acknowledgement_audits()), audits)
        self.assertEqual(self.ledger_events(), [])
        self.assertEqual(self.plan_bytes(self.plan_id), self.plan_before)

    async def test_wrong_content_type_is_415_and_keeps_the_binding(self):
        async with ingress_client(self.app) as client:
            fields = await self.review(client, self.plan_id)
            audits = len(self.acknowledgement_audits())
            response = await client.post(
                self.review_path(self.plan_id) + "/acknowledge", json=self.form(fields)
            )
            self.assertEqual(response.status_code, 415)
            self.assertIn("<h1>Invalid request</h1>", response.text)
            self.assertIn("Unsupported content type.", response.text)
            self.assertEqual(len(self.acknowledgement_audits()), audits)
            self.assertEqual(self.ledger_events(), [])
            self.assertIn((ADMIN_PRINCIPAL, "change_plan", self.plan_id), self.application._record_csrf)
            response = await self.decide(client, self.plan_id, self.form(fields))
        self.assertEqual(response.status_code, 200)

    async def test_valid_decision_is_recorded_without_touching_the_record(self):
        self.assertEqual(self.service.health_summary()["projection_failure_warning"],
                         PROJECTION_WARNING)
        calls = list(self.gateway.calls)
        async with ingress_client(self.app) as client:
            fields = await self.review(client, self.plan_id)
            with no_home_assistant(self):
                response = await self.decide(client, self.plan_id, self.form(fields))
        self.assertEqual(response.status_code, 200)
        self.assertIn("<h1>Decision recorded</h1>", response.text)
        self.assertIn("acknowledged", response.text)
        events = self.ledger_events()
        self.assertEqual(len(events), 1)
        body = events[0]["body"]
        self.assertEqual(body["principal"], ADMIN_PRINCIPAL)
        self.assertEqual(body["record_id"], self.plan_id)
        self.assertEqual(body["classification"], "historical_record")
        self.assertEqual(body["reason"], REASON)
        self.assertEqual(body["record_fingerprint"], fields["record_fingerprint"])
        self.assertIsNone(self.service.health_summary()["projection_failure_warning"])
        self.assertEqual(self.plan_bytes(self.plan_id), self.plan_before)
        self.assertEqual(self.gateway.calls, calls)
        self.assertEqual(self.application._record_csrf, {})

    async def test_form_fingerprint_mismatch_is_refused_and_consumes_the_token(self):
        async with ingress_client(self.app) as client:
            fields = await self.review(client, self.plan_id)
            audits = len(self.acknowledgement_audits())
            response = await self.decide(
                client, self.plan_id, self.form(fields, record_fingerprint="0" * 64)
            )
            self.assert_invalid_request(response, audits)
            response = await self.decide(client, self.plan_id, self.form(fields))
            self.assert_invalid_request(response, audits + 1)

    async def test_service_refusal_is_409_and_records_nothing(self):
        cases = (
            ({"reason": ""}, "reason_invalid"),
            ({"classification": "urgent"}, "classification_invalid"),
            ({"action": "delete"}, "action_invalid"),
            ({"action": "revoke", "classification": ""}, "record_not_acknowledged"),
        )
        async with ingress_client(self.app) as client:
            for overrides, reason in cases:
                with self.subTest(reason=reason):
                    fields = await self.review(client, self.plan_id)
                    with no_home_assistant(self):
                        response = await self.decide(
                            client, self.plan_id, self.form(fields, **overrides)
                        )
                    self.assertEqual(response.status_code, 409)
                    self.assertIn("<h1>Acknowledgement refused</h1>", response.text)
                    self.assertIn(reason, response.text)
                    self.assertIn("Nothing was recorded.", response.text)
                    self.assertEqual(self.acknowledgement_audits()[-1]["error_code"], reason)
        self.assertEqual(self.ledger_events(), [])
        self.assertEqual(self.plan_bytes(self.plan_id), self.plan_before)


class IngressRecordsInboxTests(Hamcp253IngressCase):
    async def test_records_page_lists_identifiers_by_state(self):
        unacknowledged, historical, defect = await self.projection_failed_plans(3)
        for record_id, classification in ((historical, "historical_record"),
                                          (defect, "current_defect")):
            outcome = self.acknowledge("change_plan", record_id, classification=classification)
            self.assertEqual(outcome["status"], "acknowledged")
        app = self.mount()
        async with ingress_client(app) as client:
            with no_home_assistant(self):
                response = await client.get("/records")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("<h1>Stuck governance records</h1>", html)
        for record_id, state in ((unacknowledged, "unacknowledged"),
                                 (historical, "acknowledged: historical record"),
                                 (defect, "acknowledged: current defect")):
            with self.subTest(state=state):
                self.assertIn(
                    f'<li><a href="{INGRESS_PATH}/records/change_plan/{record_id}">'
                    f"{record_id}</a> — {state}</li>",
                    html,
                )
        self.assertIn("Acknowledgement ledger: ok", html)
        self.assertIn("<h2>Execution tasks requiring manual review</h2><p>None.</p>", html)
        self.assertEqual(self.application._record_csrf, {})
        self.assertEqual(len(self.ledger_events()), 2)


if __name__ == "__main__":
    unittest.main()
