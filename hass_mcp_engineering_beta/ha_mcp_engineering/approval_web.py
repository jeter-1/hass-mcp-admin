"""Admin-only Home Assistant Ingress approval interface.

The MCP listener never mounts these routes. Home Assistant authenticates the
Ingress session and ``panel_admin`` limits the panel to administrators; this
application additionally accepts requests only from the documented Supervisor
Ingress peer and requires the documented Ingress path header.
"""

from __future__ import annotations

import asyncio
import hashlib
from html import escape
import hmac
import re
import secrets
from typing import Any
import unicodedata
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from .errors import GovernanceError
from .governance.service import (
    DEFAULT_APPROVER_PRINCIPAL,
    MAX_APPROVAL_PROJECTION_ACTIONS_PER_PLAN,
    MAX_APPROVAL_PROJECTION_CONTROLS,
    MAX_APPROVAL_PROJECTION_DATA,
    MAX_APPROVAL_PROJECTION_DETAILS_PER_PLAN,
    MAX_APPROVAL_PROJECTION_METADATA,
    MAX_APPROVAL_PROJECTION_STEPS,
    MAX_APPROVAL_PROJECTION_TARGETS,
)


MAX_BODY_BYTES = 8_192
MAX_HTML_BYTES = 100_000
MAX_REQUEST_SECONDS = 5
MAX_REVIEW_OPERATIONS = 8
MAX_STEP_CHANGED_FIELDS = 20
MAX_STEP_WARNINGS = 10
MAX_SEMANTIC_METADATA_FIELDS = MAX_APPROVAL_PROJECTION_METADATA
MAX_SEMANTIC_ACTIONS_PER_OPERATION = MAX_APPROVAL_PROJECTION_STEPS
MAX_SEMANTIC_CONTROLS_PER_OPERATION = MAX_APPROVAL_PROJECTION_CONTROLS
MAX_SEMANTIC_ACTIONS_PER_PLAN = MAX_APPROVAL_PROJECTION_ACTIONS_PER_PLAN
MAX_SEMANTIC_TARGETS_PER_ACTION = MAX_APPROVAL_PROJECTION_TARGETS
MAX_SEMANTIC_DATA_PER_ACTION = MAX_APPROVAL_PROJECTION_DATA
MAX_SEMANTIC_DETAILS_PER_PLAN = MAX_APPROVAL_PROJECTION_DETAILS_PER_PLAN
SUPERVISOR_INGRESS_PEER = "172.30.32.2"
_INGRESS_PATH = re.compile(r"^/api/hassio_ingress/[A-Za-z0-9_-]{8,128}$")
_HA_USER_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; img-src 'none'"
    ),
}


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response


class IngressApprovalApplication:
    def __init__(
        self,
        governance,
        *,
        allowed_peers: tuple[str, ...] = (SUPERVISOR_INGRESS_PEER,),
        require_ingress_header: bool = True,
    ):
        self.governance = governance
        self.allowed_peers = frozenset(allowed_peers)
        self.require_ingress_header = require_ingress_header
        self._f3_csrf: dict[str, tuple[str, str, int, str, str]] = {}

    def create(self) -> Starlette:
        app = Starlette(
            debug=False,
            routes=[
                Route("/", self.inbox, methods=["GET"]),
                Route("/plans/{plan_id}", self.review, methods=["GET"]),
                Route("/plans/{plan_id}/approve", self.approve, methods=["POST"]),
                Route("/plans/{plan_id}/reject", self.reject, methods=["POST"]),
                Route("/f3", self.f3_inbox, methods=["GET"]),
                Route("/f3/{child_id}", self.f3_review, methods=["GET"]),
                Route("/f3/{child_id}/reconcile", self.f3_reconcile, methods=["POST"]),
            ],
        )
        app.add_middleware(_SecurityHeadersMiddleware)
        return app

    def _ingress_context(self, request: Request) -> tuple[str, str] | None:
        peer = request.client.host if request.client else ""
        if self.allowed_peers and peer not in self.allowed_peers:
            return None
        prefix = request.headers.get("x-ingress-path", "")
        if self.require_ingress_header and not _INGRESS_PATH.fullmatch(prefix):
            return None
        if not prefix:
            prefix = ""
        user_id = request.headers.get("x-remote-user-id", "")
        principal = (
            f"home_assistant_admin_ingress:{user_id}"
            if _HA_USER_ID.fullmatch(user_id)
            else DEFAULT_APPROVER_PRINCIPAL
        )
        return prefix, principal

    @staticmethod
    def _response(body: str, status: int = 200) -> HTMLResponse:
        encoded = body.encode("utf-8")
        if len(encoded) > MAX_HTML_BYTES:
            body = _page(
                "Approval interface error",
                "<p>The bounded approval response exceeded its safe size.</p>",
            )
            status = 500
        return HTMLResponse(body, status_code=status)

    def _deny_unless_ingress(self, request: Request) -> tuple[str, str] | Response:
        context = self._ingress_context(request)
        if context is None:
            return self._response(
                _page("Forbidden", "<p>This interface is available only through Home Assistant Ingress.</p>"),
                403,
            )
        return context

    async def inbox(self, request: Request) -> Response:
        context = self._deny_unless_ingress(request)
        if isinstance(context, Response):
            return context
        prefix, _ = context
        reviews = self.governance.require().pending_external_reviews()
        if not reviews:
            content = "<p>No governed plans are awaiting external approval.</p>"
        else:
            rows = []
            for review in reviews[:100]:
                plan_id = _text(review.get("plan_id"), 64)
                title = _text(review.get("title"), 160)
                operations, projection_error = _bounded_operation_summaries(review)
                if "operation_summaries" in review:
                    target = (
                        f"{len(operations)} ordered configuration operations"
                        if not projection_error
                        else "configuration plan with an unavailable review projection"
                    )
                else:
                    target = _text(review.get("target_id"), 160)
                kind = _text(review.get("approval_kind"), 16)
                rows.append(
                    "<li><a href=\"{}\">{}</a> — {} for {}</li>".format(
                        escape(f"{prefix}/plans/{plan_id}", quote=True),
                        escape(title),
                        escape(kind),
                        escape(target),
                    )
                )
            content = "<ul>" + "".join(rows) + "</ul>"
        return self._response(_page("Pending governed approvals", content))

    async def review(self, request: Request) -> Response:
        context = self._deny_unless_ingress(request)
        if isinstance(context, Response):
            return context
        prefix, _ = context
        plan_id = request.path_params["plan_id"]
        challenge_id = ""
        for candidate in self.governance.require().pending_external_reviews():
            if candidate.get("plan_id") == plan_id:
                challenge_id = str(candidate.get("challenge_id") or "")
                break
        if not challenge_id:
            return self._response(_page("Approval unavailable", "<p>No active approval challenge exists.</p>"), 404)
        try:
            review, csrf = await self.governance.require().issue_external_csrf(plan_id, challenge_id)
        except GovernanceError as exc:
            return self._response(_page("Approval unavailable", f"<p>{escape(exc.safe_message)}</p>"), 409)
        return self._response(_render_review(prefix, review, csrf))

    async def approve(self, request: Request) -> Response:
        return await self._decide(request, "approve")

    async def reject(self, request: Request) -> Response:
        return await self._decide(request, "reject")

    async def f3_inbox(self, request: Request) -> Response:
        context = self._deny_unless_ingress(request)
        if isinstance(context, Response):
            return context
        prefix, _principal = context
        if _principal == DEFAULT_APPROVER_PRINCIPAL:
            return self._response(
                _page("Forbidden", "<p>An authenticated Home Assistant administrator identity is required.</p>"),
                403,
            )
        runtime = self.governance.require().f3_runtime
        if runtime is None:
            return self._response(_page("F3 unavailable", "<p>F3 is not ready.</p>"), 503)
        rows = []
        for item in runtime.reconciliation_items():
            child_id = _text(item["child_id"], 128)
            rows.append(
                '<li><a href="{}">{}</a> — {} — {}</li>'.format(
                    escape(f"{prefix}/f3/{child_id}", quote=True),
                    escape(child_id),
                    escape(_text(item["operation_id"], 96)),
                    escape(_text(item["normalized_outcome"] or item["state"], 96)),
                )
            )
        content = "<p>No unresolved F3 executions or holds.</p>" if not rows else "<ul>" + "".join(rows) + "</ul>"
        return self._response(_page("F3 reconciliation", content))

    async def f3_review(self, request: Request) -> Response:
        context = self._deny_unless_ingress(request)
        if isinstance(context, Response):
            return context
        prefix, principal = context
        if principal == DEFAULT_APPROVER_PRINCIPAL:
            return self._response(
                _page("Forbidden", "<p>An authenticated Home Assistant administrator identity is required.</p>"),
                403,
            )
        runtime = self.governance.require().f3_runtime
        if runtime is None:
            return self._response(_page("F3 unavailable", "<p>F3 is not ready.</p>"), 503)
        child_id = request.path_params["child_id"]
        item = next(
            (value for value in runtime.reconciliation_items() if value["child_id"] == child_id),
            None,
        )
        if item is None:
            return self._response(_page("F3 record unavailable", "<p>No unresolved record exists.</p>"), 404)
        token = secrets.token_urlsafe(32)
        hold_generation_binding = ",".join(
            f"{value['key']}:{value['generation']}"
            for value in item["hold_tokens"]
        )
        if len(self._f3_csrf) >= 128:
            self._f3_csrf.pop(next(iter(self._f3_csrf)))
        self._f3_csrf[child_id] = (
            hashlib.sha256(token.encode()).hexdigest(),
            principal,
            item["record_generation"],
            item["prepared_hash"],
            hold_generation_binding,
        )
        rows = "".join(
            f"<tr><th>{escape(label)}</th><td><code>{escape(_text(value, 1000))}</code></td></tr>"
            for label, value in (
                ("Public task", item["public_task_id"]),
                ("Plan", item["plan_id"]),
                ("Child", item["child_id"]),
                ("Operation / ordinal", f"{item['operation_id']} / {item['ordinal']}"),
                ("Target", item["target"]),
                ("Prepared hash", item["prepared_hash"]),
                ("State", item["state"]),
                ("Outcome", item["normalized_outcome"]),
                ("Intent", item["intent_timestamp"]),
                ("Evidence deadline", item["evidence_deadline"]),
                ("Dispatch count", item["dispatch_count"]),
                ("Provider response", item["provider_response_received"]),
                ("Observation / verification", f"{item['observation_count']} / {item['verification_count']}"),
                ("Selective holds", item["selective_hold_keys"]),
                ("Reason codes", item["reason_codes"]),
                ("Current readback summary", item["last_readback_summary"]),
                ("Last reconciliation", item["last_reconciliation_at"]),
            )
        )
        hidden = "".join(
            f'<input type="hidden" name="{name}" value="{escape(str(value or ""), quote=True)}">'
            for name, value in (
                ("csrf", token),
                ("record_generation", item["record_generation"]),
                ("prepared_hash", item["prepared_hash"]),
                ("hold_generation_binding", hold_generation_binding),
            )
        )
        action_url = escape(f"{prefix}/f3/{child_id}/reconcile", quote=True)
        actions = (
            "rerun_observation", "rerun_verification", "retain_hold",
            "release_hold_after_verified_resolution",
            "close_manual_review_unresolved", "create_governed_rollback_plan",
        )
        forms = "".join(
            f'<form method="post" action="{action_url}">{hidden}'
            f'<input type="hidden" name="action" value="{action}">'
            f'<button type="submit">{escape(action.replace("_", " "))}</button></form>'
            for action in actions
        )
        return self._response(_page("F3 reconciliation record", f"<table>{rows}</table>{forms}"))

    async def f3_reconcile(self, request: Request) -> Response:
        context = self._deny_unless_ingress(request)
        if isinstance(context, Response):
            return context
        _prefix, principal = context
        if principal == DEFAULT_APPROVER_PRINCIPAL:
            return self._response(
                _page("Forbidden", "<p>An authenticated Home Assistant administrator identity is required.</p>"),
                403,
            )
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            return self._response(_page("Invalid request", "<p>Unsupported content type.</p>"), 415)
        try:
            content_length = int(request.headers.get("content-length", "0"))
            if not 1 <= content_length <= MAX_BODY_BYTES:
                raise ValueError
            raw = await asyncio.wait_for(request.body(), timeout=MAX_REQUEST_SECONDS)
            if len(raw) > MAX_BODY_BYTES:
                raise ValueError
            parsed = parse_qs(raw.decode("utf-8", "strict"), keep_blank_values=True, max_num_fields=8)
            one = lambda name: parsed.get(name, [""])[0]
            child_id = request.path_params["child_id"]
            binding = self._f3_csrf.pop(child_id, None)
            supplied = hashlib.sha256(one("csrf")[:256].encode()).hexdigest()
            if binding is None or binding[1] != principal or not hmac.compare_digest(binding[0], supplied):
                raise ValueError
            generation = int(one("record_generation"))
            hold_generation_binding = one("hold_generation_binding")[:2048]
            if (
                generation,
                one("prepared_hash"),
                hold_generation_binding,
            ) != binding[2:]:
                raise ValueError
            result = await self.governance.require().f3_runtime.reconcile_child(
                child_id=child_id,
                action=one("action")[:64],
                record_generation=generation,
                prepared_hash=one("prepared_hash")[:64],
                hold_generation_binding=hold_generation_binding,
                authorized_principal=principal,
            )
        except TimeoutError:
            return self._response(_page("Request timeout", "<p>The bounded reconciliation request timed out.</p>"), 408)
        except (UnicodeDecodeError, ValueError):
            return self._response(_page("Invalid request", "<p>The bounded action is invalid or stale.</p>"), 400)
        except GovernanceError as exc:
            return self._response(_page("Reconciliation refused", f"<p>{escape(exc.safe_message)}</p>"), 409)
        return self._response(_page("Reconciliation recorded", f"<p>{escape(_text(result.get('status'), 96))}</p>"))

    async def _decide(self, request: Request, decision: str) -> Response:
        context = self._deny_unless_ingress(request)
        if isinstance(context, Response):
            return context
        _, principal = context
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            return self._response(_page("Invalid request", "<p>Unsupported content type.</p>"), 415)
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return self._response(_page("Invalid request", "<p>Invalid request length.</p>"), 400)
        if content_length < 1 or content_length > MAX_BODY_BYTES:
            return self._response(_page("Invalid request", "<p>Request body is outside the permitted bounds.</p>"), 413)
        try:
            raw = await asyncio.wait_for(request.body(), timeout=MAX_REQUEST_SECONDS)
        except TimeoutError:
            return self._response(_page("Request timeout", "<p>The bounded approval request timed out.</p>"), 408)
        if len(raw) > MAX_BODY_BYTES:
            return self._response(_page("Invalid request", "<p>Request body is outside the permitted bounds.</p>"), 413)
        try:
            parsed = parse_qs(raw.decode("utf-8", "strict"), keep_blank_values=True, max_num_fields=8)
            one = lambda name: parsed.get(name, [""])[0]
            result = await self.governance.require().decide_external_approval(
                plan_id=request.path_params["plan_id"],
                challenge_id=one("challenge_id")[:256],
                expected_plan_hash=one("plan_hash")[:128],
                approval_kind=one("approval_kind")[:16],
                # Pre-F2 standard-approval forms did not carry an action
                # discriminator. Preserve that bounded standard path while an
                # elevated acknowledgement still requires its exact action.
                approval_action=(
                    one("approval_action")[:64] or "plan_approval"
                ),
                csrf_nonce=one("csrf")[:256],
                decision=decision,
                approver_principal=principal,
            )
        except (UnicodeDecodeError, ValueError):
            return self._response(_page("Invalid request", "<p>The form data is invalid.</p>"), 400)
        except GovernanceError as exc:
            return self._response(_page("Approval refused", f"<p>{escape(exc.safe_message)}</p>"), 409)
        status = _text(result.get("status"), 32)
        return self._response(
            _page(
                "Approval decision recorded",
                f"<p>The exact governed plan was <strong>{escape(status)}</strong>.</p>",
            )
        )


def create_approval_application(
    governance,
    *,
    allowed_peers: tuple[str, ...] = (SUPERVISOR_INGRESS_PEER,),
    require_ingress_header: bool = True,
) -> Starlette:
    return IngressApprovalApplication(
        governance,
        allowed_peers=allowed_peers,
        require_ingress_header=require_ingress_header,
    ).create()


def _text(value: Any, limit: int) -> str:
    raw = str(value or "")
    safe = "".join(
        character
        if not unicodedata.category(character).startswith("C") or character in "\n\r\t"
        else "�"
        for character in raw
    )
    return safe[:limit]


def _page(title: str, content: str) -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#202124}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #bbb;padding:.5rem;text-align:left;vertical-align:top}}
code{{overflow-wrap:anywhere}}button{{padding:.65rem 1rem;margin:.5rem .5rem .5rem 0}}.danger{{color:#8b0000}}
</style></head><body><h1>{title}</h1>{content}</body></html>""".format(
        title=escape(_text(title, 160)), content=content
    )


def _bounded_operation_summaries(
    review: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Return only the service's bounded, raw-config-free review projection.

    Raw plan operations can contain complete proposed or current configuration
    and must never be rendered by the approval application.
    """

    if "operation_summaries" not in review:
        return [], None
    raw = review.get("operation_summaries")
    if not isinstance(raw, list):
        return [], "The ordered-operation review projection is malformed."
    if not 1 <= len(raw) <= MAX_REVIEW_OPERATIONS:
        return (
            [item for item in raw[:MAX_REVIEW_OPERATIONS] if isinstance(item, dict)],
            f"The plan cannot be approved because its review projection must contain 1 to {MAX_REVIEW_OPERATIONS} operations.",
        )
    if any(not isinstance(item, dict) for item in raw):
        return [], "The ordered-operation review projection is malformed."
    operations = [dict(item) for item in raw]
    action_count = 0
    detail_count = 0
    for operation in operations:
        projection = operation.get("semantic_projection")
        if not isinstance(projection, dict):
            return (
                operations,
                "The plan cannot be approved because a semantic operation projection is missing.",
            )
        if (
            projection.get("status") != "complete"
            or projection.get("redaction_applied") is True
            or projection.get("truncation_applied") is True
        ):
            return (
                operations,
                "The plan cannot be approved because a semantic operation projection is incomplete.",
            )
        metadata = projection.get("metadata")
        actions = projection.get("actions")
        controls = projection.get("controls")
        if (
            not isinstance(metadata, list)
            or len(metadata) > MAX_SEMANTIC_METADATA_FIELDS
            or any(not isinstance(item, dict) for item in metadata)
            or not isinstance(actions, list)
            or len(actions) > MAX_SEMANTIC_ACTIONS_PER_OPERATION
            or any(not isinstance(item, dict) for item in actions)
            or not isinstance(controls, list)
            or len(controls) > MAX_SEMANTIC_CONTROLS_PER_OPERATION
            or any(not isinstance(item, dict) for item in controls)
        ):
            return (
                operations,
                "The plan cannot be approved because a semantic operation projection is malformed.",
            )
        action_count += len(actions) + len(controls)
        detail_count += len(metadata)
        for entry in [*actions, *controls]:
            targets = entry.get("targets")
            data = entry.get("data")
            if (
                not isinstance(targets, list)
                or len(targets) > MAX_SEMANTIC_TARGETS_PER_ACTION
                or any(not isinstance(item, dict) for item in targets)
                or not isinstance(data, list)
                or len(data) > MAX_SEMANTIC_DATA_PER_ACTION
                or any(not isinstance(item, dict) for item in data)
            ):
                return (
                    operations,
                    "The plan cannot be approved because a semantic operation projection is malformed.",
                )
            detail_count += len(targets) + len(data)
    if (
        action_count > MAX_SEMANTIC_ACTIONS_PER_PLAN
        or detail_count > MAX_SEMANTIC_DETAILS_PER_PLAN
    ):
        return (
            operations,
            "The plan cannot be approved because its semantic operation projection exceeds the safe review bounds.",
        )
    return operations, None


def _summary_scalar(value: Any, limit: int) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        return "[structured value omitted]"
    return _text(value, limit)


def _semantic_value(value: Any, limit: int = 200) -> str:
    if isinstance(value, list):
        values = [
            _semantic_value(item, limit)
            for item in value[:8]
            if not isinstance(item, (dict, list, tuple, set))
        ]
        return ", ".join(values)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)[:limit]
    return _summary_scalar(value, limit)


def _operation_target(operation: dict[str, Any]) -> tuple[str, str]:
    target_type = operation.get("resource_type") or "unknown"
    if target_type == "helper" and operation.get("helper_type"):
        target_type = f"helper/{_summary_scalar(operation.get('helper_type'), 32)}"
    target_id = operation.get("target_id") or "unknown"
    return _summary_scalar(target_type, 64), _summary_scalar(target_id, 160)


def _operation_dependencies(operation: dict[str, Any]) -> str:
    dependencies = operation.get("depends_on", [])
    if not isinstance(dependencies, list):
        return "invalid"
    values = [
        _summary_scalar(value, 64)
        for value in dependencies[:MAX_REVIEW_OPERATIONS]
        if not isinstance(value, (dict, list, tuple, set))
    ]
    return ", ".join(values) if values else "none"


def _operation_risk(operation: dict[str, Any]) -> tuple[str, str]:
    level = operation.get("risk_level")
    if isinstance(level, (dict, list, tuple, set)):
        level = "unknown"
    reasons = operation.get("risk_reasons", [])
    if not isinstance(reasons, list):
        reasons = []
    safe_reasons = [
        _summary_scalar(value, 300)
        for value in reasons[:MAX_STEP_WARNINGS]
        if not isinstance(value, (dict, list, tuple, set))
    ]
    return _summary_scalar(level or "unknown", 32), "; ".join(safe_reasons) or "none reported"


def _operation_changed_fields(operation: dict[str, Any]) -> list[dict[str, Any]]:
    changed_fields = operation.get("changed_fields", [])
    if not isinstance(changed_fields, list):
        return []
    return [
        item
        for item in changed_fields[:MAX_STEP_CHANGED_FIELDS]
        if isinstance(item, dict)
    ]


def _render_semantic_projection(operation: dict[str, Any]) -> str:
    projection = operation.get("semantic_projection")
    if not isinstance(projection, dict):
        return "<p class=\"danger\">Semantic operation detail is unavailable.</p>"

    metadata = projection.get("metadata", [])
    metadata_rows = "".join(
        "<tr><td>{}</td><td><code>{}</code></td></tr>".format(
            escape(_summary_scalar(item.get("field"), 120)),
            escape(_semantic_value(item.get("value"))),
        )
        for item in metadata[:MAX_SEMANTIC_METADATA_FIELDS]
        if isinstance(item, dict)
    )
    metadata_block = (
        "<h4>Bounded configuration metadata</h4>"
        "<table><tr><th>Field</th><th>Proposed value</th></tr>"
        f"{metadata_rows}</table>"
        if metadata_rows
        else "<h4>Bounded configuration metadata</h4><p>None.</p>"
    )

    action_rows: list[str] = []
    actions = projection.get("actions", [])
    for action in (
        actions[:MAX_SEMANTIC_ACTIONS_PER_OPERATION]
        if isinstance(actions, list)
        else []
    ):
        if not isinstance(action, dict):
            continue
        targets = action.get("targets", [])
        target_text = "; ".join(
            "{}={}".format(
                _summary_scalar(item.get("selector"), 64),
                _semantic_value(item.get("value")),
            )
            for item in (
                targets[:MAX_SEMANTIC_TARGETS_PER_ACTION]
                if isinstance(targets, list)
                else []
            )
            if isinstance(item, dict)
        )
        data = action.get("data", [])
        data_text = "; ".join(
            "{}={}".format(
                _summary_scalar(item.get("field"), 120),
                _semantic_value(item.get("value")),
            )
            for item in (
                data[:MAX_SEMANTIC_DATA_PER_ACTION]
                if isinstance(data, list)
                else []
            )
            if isinstance(item, dict)
        )
        action_rows.append(
            "<tr><td><code>{}</code></td><td><code>{}</code></td>"
            "<td><code>{}</code></td><td><code>{}</code></td></tr>".format(
                escape(_summary_scalar(action.get("path"), 160)),
                escape(_summary_scalar(action.get("action"), 200)),
                escape(target_text or "none"),
                escape(data_text or "none"),
            )
        )
    action_block = (
        "<h4>Ordered semantic actions</h4>"
        "<table><tr><th>Path</th><th>Action or service</th>"
        "<th>Explicit targets</th><th>Key primitive data</th></tr>"
        + "".join(action_rows)
        + "</table>"
        if action_rows
        else "<h4>Ordered semantic actions</h4><p>None for this resource.</p>"
    )

    control_rows: list[str] = []
    controls = projection.get("controls", [])
    for control in (
        controls[:MAX_SEMANTIC_CONTROLS_PER_OPERATION]
        if isinstance(controls, list)
        else []
    ):
        if not isinstance(control, dict):
            continue
        targets = control.get("targets", [])
        target_text = "; ".join(
            "{}={}".format(
                _summary_scalar(item.get("selector"), 64),
                _semantic_value(item.get("value")),
            )
            for item in (
                targets[:MAX_SEMANTIC_TARGETS_PER_ACTION]
                if isinstance(targets, list)
                else []
            )
            if isinstance(item, dict)
        )
        data = control.get("data", [])
        data_text = "; ".join(
            "{}={}".format(
                _summary_scalar(item.get("field"), 120),
                _semantic_value(item.get("value")),
            )
            for item in (
                data[:MAX_SEMANTIC_DATA_PER_ACTION]
                if isinstance(data, list)
                else []
            )
            if isinstance(item, dict)
        )
        control_rows.append(
            "<tr><td><code>{}</code></td><td><code>{}</code></td>"
            "<td><code>{}</code></td><td><code>{}</code></td>"
            "<td><code>{}</code></td></tr>".format(
                escape(_summary_scalar(control.get("path"), 160)),
                escape(_summary_scalar(control.get("kind"), 32)),
                escape(_summary_scalar(control.get("type"), 200)),
                escape(target_text or "none"),
                escape(data_text or "none"),
            )
        )
    control_block = (
        "<h4>Automation triggers and conditions</h4>"
        "<table><tr><th>Path</th><th>Kind</th><th>Type</th>"
        "<th>Explicit targets</th><th>Key primitive data</th></tr>"
        + "".join(control_rows)
        + "</table>"
        if control_rows
        else (
            "<h4>Automation triggers and conditions</h4>"
            "<p>None for this resource.</p>"
        )
    )
    redaction_note = (
        "<p><strong>Secret-like values were redacted from this semantic view.</strong></p>"
        if projection.get("redaction_applied") is True
        else ""
    )
    return (
        "<h4>Semantic approval detail</h4>"
        f"{metadata_block}{action_block}{control_block}{redaction_note}"
    )


def _render_operation_summaries(operations: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for ordinal, operation in enumerate(operations, start=1):
        operation_id = _summary_scalar(
            operation.get("operation_id") or ordinal,
            64,
        )
        declared_order = operation.get("order")
        position = _summary_scalar(
            str(ordinal if declared_order is None else declared_order),
            16,
        )
        operation_name = _summary_scalar(
            operation.get("action") or "unknown",
            80,
        )
        target_type, target_id = _operation_target(operation)
        risk_level, risk_reasons = _operation_risk(operation)
        rows = [
            ("Step", position),
            ("Operation ID", operation_id),
            ("Operation", operation_name),
            ("Typed target", f"{target_type}: {target_id}"),
            ("Depends on", _operation_dependencies(operation)),
            ("Risk", risk_level),
            ("Risk reasons", risk_reasons),
            (
                "Validation",
                (
                    "valid"
                    if operation.get("validation_valid") is True
                    else "invalid"
                    if operation.get("validation_valid") is False
                    else "not reported"
                ),
            ),
            (
                "Batch rollback",
                "unavailable; remediation requires a new inspected and approved plan",
            ),
        ]
        summary_table = "<table>" + "".join(
            "<tr><th>{}</th><td><code>{}</code></td></tr>".format(
                escape(label),
                escape(value),
            )
            for label, value in rows
        ) + "</table>"

        changed_fields = _operation_changed_fields(operation)
        if changed_fields:
            change_rows = "".join(
                "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    escape(_summary_scalar(item.get("field"), 160)),
                    escape(_summary_scalar(item.get("before"), 500)),
                    escape(_summary_scalar(item.get("after"), 500)),
                )
                for item in changed_fields
            )
            diff = (
                "<h4>Bounded step diff</h4><table>"
                "<tr><th>Field</th><th>Before</th><th>After</th></tr>"
                f"{change_rows}</table>"
            )
        else:
            diff = "<h4>Bounded step diff</h4><p>No changed-field summary is available.</p>"
        semantic_detail = _render_semantic_projection(operation)

        warnings = operation.get("warnings", [])
        if not isinstance(warnings, list):
            warnings = []
        safe_warnings = [
            _summary_scalar(value, 500)
            for value in warnings[:MAX_STEP_WARNINGS]
            if not isinstance(value, (dict, list, tuple, set))
        ]
        warning_block = (
            "<h4>Step warnings</h4><ul>"
            + "".join(f"<li>{escape(value)}</li>" for value in safe_warnings)
            + "</ul>"
            if safe_warnings
            else "<h4>Step warnings</h4><p>None reported.</p>"
        )
        rendered.append(
            f"<li><h3>Step {escape(position)}: {escape(operation_name)}</h3>"
            f"{summary_table}{semantic_detail}{diff}{warning_block}</li>"
        )
    return (
        "<h2>Ordered configuration operations</h2>"
        "<p class=\"danger\"><strong>This plan is non-atomic.</strong> "
        "Apply stops on the first failure. Earlier verified changes remain in "
        "place, later steps are not attempted, and no automatic rollback occurs. "
        "Batch rollback is unavailable. After any partial result, inspect the "
        "actual Home Assistant state and create a new exact plan; remediation "
        "requires a new external approval.</p>"
        "<ol>"
        + "".join(rendered)
        + "</ol>"
    )


def _render_review(prefix: str, review: dict[str, Any], csrf: str) -> str:
    def row(label: str, value: Any) -> str:
        return f"<tr><th>{escape(label)}</th><td><code>{escape(_text(value, 2_000))}</code></td></tr>"

    operation_summaries, projection_error = _bounded_operation_summaries(review)
    has_operation_projection = "operation_summaries" in review
    rows = [
        row("Title", review.get("title")),
        row("Description", review.get("description")),
        row("Plan ID", review.get("plan_id")),
        row("Exact plan hash", review.get("plan_hash")),
        row("Plan version", review.get("plan_version")),
        row("Approval kind", review.get("approval_kind")),
        row(
            "Operation",
            (
                "ordered_configuration_plan"
                if has_operation_projection
                else review.get("operation")
            ),
        ),
        row(
            "Target",
            (
                f"{len(operation_summaries)} typed operations"
                if has_operation_projection and not projection_error
                else (
                    "review projection unavailable"
                    if has_operation_projection
                    else f"{review.get('target_type')}: {review.get('target_id')}"
                )
            ),
        ),
        row("Risk", review.get("risk_level")),
        row("Policy class", review.get("policy_class")),
        row("Risk delta", review.get("risk_delta")),
        row("Physical consequence", review.get("physical_consequence")),
        row("Policy reason codes", review.get("policy_reason_codes")),
        row("Policy decision hash", review.get("policy_decision_hash")),
        row("Administrator action", review.get("approval_action")),
        row("Approval bundle state", review.get("approval_bundle_state")),
        row(
            "Same administrator required",
            review.get("same_principal_requirement"),
        ),
        row("Plan expiration", review.get("expires_at")),
        row("Challenge expiration", review.get("challenge_expires_at")),
        row("MCP request note (not human approval)", review.get("request_note")),
        row("Validation", "valid" if review.get("validation_valid") else "invalid"),
        row("Apply currently allowed", review.get("apply_allowed")),
        row("Approval state", review.get("approval_state")),
    ]
    if review.get("approval_kind") == "rollback":
        rows.extend(
            [
                row("Original apply timestamp", review.get("original_apply_timestamp")),
                row("Current post-apply fingerprint", review.get("current_post_apply_fingerprint")),
                row("Snapshot fingerprint", review.get("snapshot_fingerprint")),
                row("Rollback target", review.get("rollback_target")),
            ]
        )
    changes = review.get("changed_fields") or []
    if has_operation_projection and not projection_error:
        change_table = _render_operation_summaries(operation_summaries)
    elif has_operation_projection:
        change_table = (
            "<h2>Ordered configuration operations</h2>"
            f"<p class=\"danger\"><strong>{escape(projection_error or 'The review projection is unavailable.')}</strong> "
            "Approval is disabled. Reject this plan and create a new bounded plan.</p>"
        )
    elif changes:
        change_rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                escape(_summary_scalar(item.get("field"), 160)),
                escape(_summary_scalar(item.get("before"), 500)),
                escape(_summary_scalar(item.get("after"), 500)),
            )
            for item in changes[:50]
            if isinstance(item, dict)
        )
        change_table = "<h2>Bounded change summary</h2><table><tr><th>Field</th><th>Before</th><th>After</th></tr>" + change_rows + "</table>"
    else:
        change_table = "<h2>Bounded change summary</h2><p>No changed-field summary is available.</p>"
    warnings = "".join(f"<li>{escape(_text(value, 500))}</li>" for value in (review.get("warnings") or [])[:20])
    warning_block = f"<h2>Warnings</h2><ul>{warnings}</ul>" if warnings else "<h2>Warnings</h2><p>None reported.</p>"
    hidden = "".join(
        f'<input type="hidden" name="{escape(name, quote=True)}" value="{escape(_text(value, 256), quote=True)}">'
        for name, value in (
            ("challenge_id", review.get("challenge_id")),
            ("plan_hash", review.get("plan_hash")),
            ("approval_kind", review.get("approval_kind")),
            ("approval_action", review.get("approval_action")),
            ("csrf", csrf),
        )
    )
    plan_id = escape(_text(review.get("plan_id"), 64), quote=True)
    approve_action = escape(f"{prefix}/plans/{plan_id}/approve", quote=True)
    reject_action = escape(f"{prefix}/plans/{plan_id}/reject", quote=True)
    elevated_acknowledgement = (
        review.get("approval_action")
        == "elevated_risk_acknowledgement"
    )
    action_label = (
        "Acknowledge elevated risk"
        if elevated_acknowledgement
        else "Approve exact plan"
    )
    action_explanation = (
        "The same authenticated Home Assistant administrator who approved "
        "the plan must separately acknowledge the displayed elevated risk "
        "and physical consequence."
        if elevated_acknowledgement
        else "This action approves the exact plan. Elevated plans require a "
        "separate acknowledgement on a subsequent page."
    )
    approve_form = (
        f'<p>{escape(action_explanation)}</p>'
        f'<form method="post" action="{approve_action}">{hidden}<button type="submit">{escape(action_label)}</button></form>'
        if not projection_error
        else ""
    )
    forms = (
        approve_form
        + f'<form method="post" action="{reject_action}">{hidden}<button class="danger" type="submit">Reject plan</button></form>'
        + "<p>Approval is bound to the exact hash and approval kind shown above. This page does not apply or roll back the change.</p>"
    )
    return _page("Review governed change", "<table>" + "".join(rows) + "</table>" + change_table + warning_block + forms)
