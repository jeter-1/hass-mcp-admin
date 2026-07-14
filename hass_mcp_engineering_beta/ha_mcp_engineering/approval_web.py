"""Admin-only Home Assistant Ingress approval interface.

The MCP listener never mounts these routes. Home Assistant authenticates the
Ingress session and ``panel_admin`` limits the panel to administrators; this
application additionally accepts requests only from the documented Supervisor
Ingress peer and requires the documented Ingress path header.
"""

from __future__ import annotations

import asyncio
from html import escape
import re
from typing import Any
import unicodedata
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from .errors import GovernanceError
from .governance.service import DEFAULT_APPROVER_PRINCIPAL


MAX_BODY_BYTES = 8_192
MAX_HTML_BYTES = 100_000
MAX_REQUEST_SECONDS = 5
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

    def create(self) -> Starlette:
        app = Starlette(
            debug=False,
            routes=[
                Route("/", self.inbox, methods=["GET"]),
                Route("/plans/{plan_id}", self.review, methods=["GET"]),
                Route("/plans/{plan_id}/approve", self.approve, methods=["POST"]),
                Route("/plans/{plan_id}/reject", self.reject, methods=["POST"]),
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


def _render_review(prefix: str, review: dict[str, Any], csrf: str) -> str:
    def row(label: str, value: Any) -> str:
        return f"<tr><th>{escape(label)}</th><td><code>{escape(_text(value, 2_000))}</code></td></tr>"

    rows = [
        row("Title", review.get("title")),
        row("Description", review.get("description")),
        row("Plan ID", review.get("plan_id")),
        row("Exact plan hash", review.get("plan_hash")),
        row("Plan version", review.get("plan_version")),
        row("Approval kind", review.get("approval_kind")),
        row("Operation", review.get("operation")),
        row("Target", f"{review.get('target_type')}: {review.get('target_id')}"),
        row("Risk", review.get("risk_level")),
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
    if changes:
        change_rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                escape(_text(item.get("field"), 160)),
                escape(_text(item.get("before"), 500)),
                escape(_text(item.get("after"), 500)),
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
            ("csrf", csrf),
        )
    )
    plan_id = escape(_text(review.get("plan_id"), 64), quote=True)
    approve_action = escape(f"{prefix}/plans/{plan_id}/approve", quote=True)
    reject_action = escape(f"{prefix}/plans/{plan_id}/reject", quote=True)
    forms = (
        f'<form method="post" action="{approve_action}">{hidden}<button type="submit">Approve exact plan</button></form>'
        f'<form method="post" action="{reject_action}">{hidden}<button class="danger" type="submit">Reject plan</button></form>'
        "<p>Approval is bound to the exact hash and approval kind shown above. This page does not apply or roll back the change.</p>"
    )
    return _page("Review governed change", "<table>" + "".join(rows) + "</table>" + change_table + warning_block + forms)
