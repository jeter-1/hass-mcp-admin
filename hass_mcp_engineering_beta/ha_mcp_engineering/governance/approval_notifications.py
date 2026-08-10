"""Best-effort mobile notifications for persisted approval challenges.

Notifications are navigation hints only.  They never carry approval authority,
never accept a decision, and never alter the persisted governance lifecycle.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import logging
import re
from typing import Any, Awaitable, Callable

from ..audit import AuditLogger
from ..errors import (
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ..logging_config import get_logger, log_event
from ..providers.supervisor_self import SelfAddonIdentityError
from .models import ApprovalActionKind, ChangePlan


MAX_NOTIFICATION_QUEUE = 256
MAX_NOTIFICATION_STATES = 512
MOBILE_NOTIFY_SERVICE = re.compile(
    r"^notify\.mobile_app_[a-z0-9_]{1,128}$"
)
_SAFE_ADDON_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_SEND_EVENTS = {
    "external_approval_requested": ApprovalActionKind.PLAN_APPROVAL.value,
    "elevated_risk_acknowledgement_requested": (
        ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT.value
    ),
}
_CLEAR_ALL_EVENTS = {
    "external_approval_rejected",
    "external_approval_expired",
    "external_approval_invalidated",
    "external_approval_consumed",
}
_CLEAR_ACTION_EVENTS = {
    "external_approval_granted": ApprovalActionKind.PLAN_APPROVAL.value,
    "elevated_risk_acknowledgement_granted": (
        ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT.value
    ),
}


def validate_notification_service(value: str) -> bool:
    """Return whether an optional service is empty or in the sole allowlist."""

    return not value or MOBILE_NOTIFY_SERVICE.fullmatch(value) is not None


@dataclass(frozen=True)
class _NotificationWork:
    operation: str
    notification_key: str
    plan_id: str
    approval_action: str
    request_id: str | None


class ApprovalNotificationManager:
    """Queue one bounded mobile notification side effect per lifecycle event."""

    def __init__(
        self,
        rest_client: Any,
        audit: AuditLogger | None,
        *,
        service: str,
        timeout_seconds: float,
        addon_identity_resolver: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        if not validate_notification_service(service):
            raise ValueError("approval notification service is outside the allowlist")
        self.rest_client = rest_client
        self.audit = audit
        self.service = service
        self.timeout_seconds = min(max(float(timeout_seconds), 0.1), 10.0)
        self.addon_identity_resolver = addon_identity_resolver
        self.queue: asyncio.Queue[_NotificationWork] = asyncio.Queue(
            maxsize=MAX_NOTIFICATION_QUEUE
        )
        self.logger = get_logger("approval_notifications")
        self._scheduled: set[tuple[str, str]] = set()
        self._notification_attempted: OrderedDict[str, None] = OrderedDict()
        self._states: OrderedDict[str, str] = OrderedDict()
        self._worker_running = False
        self._addon_slug: str | None = None
        self._addon_identity_status = "unresolved"
        self._addon_identity_failure_category: str | None = None
        self._counters = {
            "queued": 0,
            "submitted": 0,
            "delivered": 0,
            "failed": 0,
            "clear_queued": 0,
            "clear_submitted": 0,
            "cleared": 0,
            "clear_failed": 0,
            "queue_full": 0,
            "startup_reconciliations": 0,
            "startup_reconciliation_skipped": 0,
        }
        self._last_failure_category: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.service)

    @staticmethod
    def _notification_key(challenge_id: str) -> str:
        digest = hashlib.sha256(challenge_id.encode("utf-8")).hexdigest()[:24]
        return f"ha_mcp_approval_{digest}"

    @staticmethod
    def _challenge_for_action(
        plan: ChangePlan, approval_action: str
    ) -> str | None:
        if approval_action == ApprovalActionKind.PLAN_APPROVAL.value:
            return plan.approval.challenge_id
        acknowledgement = plan.approval.elevated_risk_acknowledgement
        if (
            approval_action
            == ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT.value
            and acknowledgement is not None
        ):
            return acknowledgement.challenge_id
        return None

    def observe(
        self,
        plan: ChangePlan,
        event: str,
        *,
        request_id: str | None,
        approval_action: str | None,
    ) -> None:
        """Observe a successfully persisted event without affecting its result."""

        try:
            if not self.configured:
                return
            send_action = _SEND_EVENTS.get(event)
            if send_action is not None:
                challenge_id = self._challenge_for_action(plan, send_action)
                if challenge_id:
                    self._enqueue(
                        "notify", plan.plan_id, challenge_id, send_action, request_id
                    )
                return
            clear_action = _CLEAR_ACTION_EVENTS.get(event)
            if clear_action is not None:
                challenge_id = self._challenge_for_action(plan, clear_action)
                if challenge_id:
                    self._enqueue(
                        "clear", plan.plan_id, challenge_id, clear_action, request_id
                    )
                return
            if event in _CLEAR_ALL_EVENTS:
                for action in (
                    ApprovalActionKind.PLAN_APPROVAL.value,
                    ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT.value,
                ):
                    challenge_id = self._challenge_for_action(plan, action)
                    if challenge_id:
                        self._enqueue(
                            "clear", plan.plan_id, challenge_id, action, request_id
                        )
        except Exception as exc:  # notification state must never affect authority
            self._counters["failed"] += 1
            self._last_failure_category = "internal_error"
            log_event(
                self.logger,
                logging.WARNING,
                "approval_notification_observation_failed",
                "The advisory notification event could not be queued.",
                context={"error_type": type(exc).__name__},
            )

    def reconcile_pending(self, reviews: list[dict[str, Any]]) -> None:
        """Replace notifications for active persisted challenges after restart."""

        if not self.configured:
            return
        self._counters["startup_reconciliations"] += 1
        self._counters["startup_reconciliation_skipped"] += max(
            0, len(reviews) - MAX_NOTIFICATION_QUEUE
        )
        for review in reviews[:MAX_NOTIFICATION_QUEUE]:
            plan_id = review.get("plan_id")
            challenge_id = review.get("challenge_id")
            approval_action = review.get("approval_action")
            if (
                isinstance(plan_id, str)
                and isinstance(challenge_id, str)
                and isinstance(approval_action, str)
                and approval_action
                in {
                    ApprovalActionKind.PLAN_APPROVAL.value,
                    ApprovalActionKind.ELEVATED_RISK_ACKNOWLEDGEMENT.value,
                }
            ):
                self._enqueue(
                    "notify",
                    plan_id[:64],
                    challenge_id[:256],
                    approval_action,
                    None,
                )

    def status_for(self, challenge_id: str | None) -> dict[str, Any]:
        if not self.configured:
            status = "disabled"
        elif not challenge_id:
            status = "unavailable"
        else:
            status = self._states.get(
                self._notification_key(challenge_id), "not_scheduled"
            )
        return {
            "configured": self.configured,
            "status": status,
            "authority": "none",
            "delivery_semantics": "best_effort_advisory",
            "submission_semantics": "home_assistant_service_response_only",
            "handset_delivery_observable": False,
            "handset_clear_observable": False,
            "addon_identity_status": self._addon_identity_status,
            "addon_identity_failure_category": (
                self._addon_identity_failure_category
            ),
            "approval_performed": False,
        }

    def health_snapshot(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "provider": "home_assistant_mobile_app_notify",
            "route": "direct_home_assistant_rest_allowlisted_notification",
            "authority": "none",
            "delivery_semantics": "best_effort_advisory",
            "submission_semantics": "home_assistant_service_response_only",
            "handset_delivery_observable": False,
            "handset_clear_observable": False,
            "addon_identity_status": self._addon_identity_status,
            "addon_identity_failure_category": (
                self._addon_identity_failure_category
            ),
            "worker_running": self._worker_running,
            "queue_depth": self.queue.qsize(),
            "queue_limit": MAX_NOTIFICATION_QUEUE,
            "state_entries": len(self._states),
            "state_limit": MAX_NOTIFICATION_STATES,
            "last_failure_category": self._last_failure_category,
            "fallback": "none",
            "fallback_count": 0,
            **self._counters,
        }

    def _enqueue(
        self,
        operation: str,
        plan_id: str,
        challenge_id: str,
        approval_action: str,
        request_id: str | None,
    ) -> None:
        key = self._notification_key(challenge_id)
        scheduled = (operation, key)
        if scheduled in self._scheduled:
            return
        if operation == "notify" and key in self._notification_attempted:
            return
        work = _NotificationWork(
            operation=operation,
            notification_key=key,
            plan_id=plan_id[:64],
            approval_action=approval_action,
            request_id=request_id[:128] if isinstance(request_id, str) else None,
        )
        try:
            self.queue.put_nowait(work)
        except asyncio.QueueFull:
            self._counters["queue_full"] += 1
            self._counters[
                "failed" if operation == "notify" else "clear_failed"
            ] += 1
            self._last_failure_category = "queue_full"
            self._set_state(key, "failed_queue_full")
            self._audit(work, "failure", "queue_full", dispatched=False)
            return
        self._scheduled.add(scheduled)
        counter = "queued" if operation == "notify" else "clear_queued"
        self._counters[counter] += 1
        self._set_state(key, "queued" if operation == "notify" else "clear_queued")
        self._audit(work, "queued", None, dispatched=False)

    def _set_state(self, key: str, state: str) -> None:
        self._states[key] = state
        self._states.move_to_end(key)
        while len(self._states) > MAX_NOTIFICATION_STATES:
            self._states.popitem(last=False)

    async def run(self) -> None:
        if not self.configured:
            return
        self._worker_running = True
        try:
            while True:
                await self.process_next()
        finally:
            self._worker_running = False

    async def process_next(self) -> None:
        work = await self.queue.get()
        try:
            if work.operation == "notify":
                self._notification_attempted[work.notification_key] = None
                self._notification_attempted.move_to_end(work.notification_key)
                while len(self._notification_attempted) > MAX_NOTIFICATION_STATES:
                    self._notification_attempted.popitem(last=False)
            await self._dispatch(work)
        finally:
            self._scheduled.discard((work.operation, work.notification_key))
            self.queue.task_done()

    async def _dispatch(self, work: _NotificationWork) -> None:
        path = f"/services/notify/{self.service.split('.', 1)[1]}"
        if work.operation == "notify":
            addon_slug = await self._resolve_addon_slug()
            if addon_slug is None:
                self._failed(
                    work,
                    self._addon_identity_failure_category
                    or "configuration_unavailable",
                    response_received=False,
                    dispatched=False,
                )
                return
            review_url = (
                f"/hassio/ingress/{addon_slug}/plans/{work.plan_id}"
            )
            body = {
                "title": "Home Assistant approval requested",
                "message": (
                    "A governed Home Assistant change is waiting for "
                    "administrator review."
                ),
                "data": {
                    "tag": work.notification_key,
                    "url": review_url,
                    "clickAction": review_url,
                    "actions": [
                        {
                            "action": "URI",
                            "title": "Open Approval Panel",
                            "uri": review_url,
                        }
                    ],
                },
            }
        else:
            body = {
                "message": "clear_notification",
                "data": {"tag": work.notification_key},
            }
        try:
            await asyncio.wait_for(
                self.rest_client.request("POST", path, body=body),
                timeout=self.timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError, HomeAssistantTimeoutError):
            self._failed(work, "provider_timeout", response_received=False)
        except HomeAssistantUnavailableError:
            self._failed(work, "provider_unavailable", response_received=False)
        except HomeAssistantApiError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            status = details.get("status")
            category = (
                "authentication_failure"
                if status in {401, 403}
                else "provider_rejected"
            )
            self._failed(work, category, response_received=True)
        except Exception:
            self._failed(work, "internal_error", response_received=False)
        else:
            counter = (
                "submitted"
                if work.operation == "notify"
                else "clear_submitted"
            )
            state = (
                "submitted"
                if work.operation == "notify"
                else "clear_submitted"
            )
            self._counters[counter] += 1
            self._set_state(work.notification_key, state)
            self._audit(
                work,
                "success",
                None,
                dispatched=True,
                response_received=True,
            )

    async def _resolve_addon_slug(self) -> str | None:
        if self._addon_slug is not None:
            return self._addon_slug
        if self.addon_identity_resolver is None:
            self._addon_identity_status = "unavailable"
            self._addon_identity_failure_category = (
                "configuration_unavailable"
            )
            return None
        try:
            identity = await asyncio.wait_for(
                self.addon_identity_resolver(), timeout=self.timeout_seconds
            )
            slug = getattr(identity, "slug", None)
        except (asyncio.TimeoutError, TimeoutError):
            self._addon_identity_status = "unavailable"
            self._addon_identity_failure_category = "timeout"
            return None
        except SelfAddonIdentityError as exc:
            self._addon_identity_status = "unavailable"
            self._addon_identity_failure_category = exc.failure_category
            return None
        except Exception:
            self._addon_identity_status = "unavailable"
            self._addon_identity_failure_category = "transport_failure"
            return None
        if not isinstance(slug, str) or _SAFE_ADDON_SLUG.fullmatch(slug) is None:
            self._addon_identity_status = "unavailable"
            self._addon_identity_failure_category = "malformed_response"
            return None
        self._addon_slug = slug
        self._addon_identity_status = "verified_supervisor_self_info"
        self._addon_identity_failure_category = None
        return slug

    def _failed(
        self,
        work: _NotificationWork,
        category: str,
        *,
        response_received: bool,
        dispatched: bool = True,
    ) -> None:
        counter = "failed" if work.operation == "notify" else "clear_failed"
        self._counters[counter] += 1
        self._last_failure_category = category
        self._set_state(work.notification_key, f"{work.operation}_failed")
        self._audit(
            work,
            "failure",
            category,
            dispatched=dispatched,
            response_received=response_received,
        )

    def _audit(
        self,
        work: _NotificationWork,
        result_status: str,
        failure_category: str | None,
        *,
        dispatched: bool,
        response_received: bool | None = None,
    ) -> None:
        event = f"approval_notification_{work.operation}"
        if result_status == "success":
            event += "_submitted"
        elif result_status == "failure":
            event += "_failed"
        else:
            event += "_queued"
        record = {
            "event": event,
            "request_id": work.request_id,
            "tool_name": None,
            "capability_classification": "notification_only",
            "operation_category": "approval_notification",
            "access": "write",
            "authenticated": True,
            "plan_id": work.plan_id,
            "approval_action": work.approval_action,
            "notification_key": work.notification_key,
            "provider": "home_assistant_mobile_app_notify",
            "provider_dispatch_occurred": dispatched,
            "provider_response_received": response_received,
            "result_status": result_status,
            "failure_category": failure_category,
            "fallback": "none",
            "fallback_occurred": False,
            "approval_authority_changed": False,
            "submission_semantics": "home_assistant_service_response_only",
            "handset_outcome_observable": False,
        }
        if self.audit:
            self.audit.write(record)
        log_event(
            self.logger,
            (
                logging.INFO
                if result_status in {"success", "queued"}
                else logging.WARNING
            ),
            event,
            "Advisory approval notification lifecycle event.",
            context={
                "result_status": result_status,
                "failure_category": failure_category,
                "provider_dispatch_occurred": dispatched,
                "provider_response_received": response_received,
                "fallback_occurred": False,
            },
        )
