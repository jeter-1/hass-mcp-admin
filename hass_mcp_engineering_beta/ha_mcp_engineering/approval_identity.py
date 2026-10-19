"""Shared Home Assistant administrator verification for Ingress approval pages.

HAMCP-205 (absorbs HAMCP-199). Every Ingress approval surface asks this module,
on every request, whether the Supervisor-forwarded Home Assistant user is an
administrator. Nothing here trusts a header, the panel's ``panel_admin``
visibility or a remembered result:

- the identity is the single, well-formed ``X-Remote-User-Id`` value that
  Supervisor Ingress forwards (a missing, repeated or malformed value is
  refused before any lookup);
- the role is read from Home Assistant Core for that request only, through the
  fixed admin-only WebSocket command ``config/auth/list``, and recomputed with
  Core's own ``User.is_admin`` rule (Core 2026.10.x
  ``homeassistant/auth/models.py``: ``is_owner or (is_active and
  "system-admin" in group_ids)``), made stricter in one place: an inactive
  user, the owner included, is never an administrator;
- anything other than one positive, unambiguous administrator result refuses.

The command is source text with no caller data, so no request value can choose
a different command. Only the requester's ID and the boolean verdict are kept;
the user list (names, usernames, credential types) is never stored or logged.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from enum import Enum
import re
import threading
from types import MappingProxyType
from typing import Any, Iterable

from .clients.single_read import SINGLE_READ


# Core ``homeassistant/auth/const.py`` GROUP_ID_ADMIN.
HOME_ASSISTANT_ADMIN_GROUP = "system-admin"
# The one reviewed role-lookup command. Core requires an administrator caller
# (the Supervisor-proxied Engineering connection authenticates as Core's
# Supervisor system user, which Core keeps in the admin group).
AUTH_LIST_COMMAND = MappingProxyType({"type": "config/auth/list"})
ADMIN_INGRESS_PRINCIPAL_PREFIX = "home_assistant_admin_ingress:"
HA_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_LOOKUP_SECONDS = 5.0
MAX_USERS = 1_000


class AdministratorVerdict(str, Enum):
    ADMINISTRATOR = "administrator"
    IDENTITY_MISSING = "identity_missing"
    NOT_ADMINISTRATOR = "not_administrator"
    LOOKUP_FAILED = "lookup_failed"


REFUSAL_VERDICTS = (
    AdministratorVerdict.IDENTITY_MISSING,
    AdministratorVerdict.NOT_ADMINISTRATOR,
    AdministratorVerdict.LOOKUP_FAILED,
)


def ingress_user_id(values: Iterable[str]) -> str | None:
    """Return the one well-formed forwarded user ID, or ``None``.

    Supervisor writes its own ``X-Remote-User-Id``; a second value can only
    come from the client, so more than one value is refused rather than
    choosing between them.
    """

    values = list(values)
    if len(values) != 1 or not HA_USER_ID_PATTERN.fullmatch(values[0]):
        return None
    return values[0]


def administrator_principal(user_id: str) -> str:
    return ADMIN_INGRESS_PRINCIPAL_PREFIX + user_id


def administrator_verdict(result: Any, user_id: str) -> AdministratorVerdict:
    """Apply Core's ``is_admin`` rule, restricted to active users, to one
    ``config/auth/list`` result."""

    failed = AdministratorVerdict.LOOKUP_FAILED
    if type(result) is not list or len(result) > MAX_USERS:
        return failed
    matches = []
    for row in result:
        if type(row) is not dict or type(row.get("id")) is not str:
            return failed
        if row["id"] == user_id:
            matches.append(row)
    if len(matches) != 1:
        # Unknown or duplicated: the role cannot be established.
        return failed
    row = matches[0]
    group_ids = row.get("group_ids")
    if (
        any(type(row.get(name)) is not bool
            for name in ("is_owner", "is_active", "system_generated"))
        or type(group_ids) is not list
        or any(type(group) is not str for group in group_ids)
    ):
        return failed
    if row["system_generated"]:
        # Integration/system accounts never decide through Ingress.
        return AdministratorVerdict.NOT_ADMINISTRATOR
    if not row["is_active"]:
        # Defense in depth (HAMCP-205 L1): an inactive user, the owner
        # included, never decides, although Core's is_admin keeps an inactive
        # owner an administrator.
        return AdministratorVerdict.NOT_ADMINISTRATOR
    if row["is_owner"] or HOME_ASSISTANT_ADMIN_GROUP in group_ids:
        return AdministratorVerdict.ADMINISTRATOR
    return AdministratorVerdict.NOT_ADMINISTRATOR


class AdministratorVerifier:
    """Per-request role lookup. It keeps no result between calls."""

    def __init__(self, websocket_client, *, timeout_seconds: float = MAX_LOOKUP_SECONDS):
        self._websocket_client = websocket_client
        self._timeout_seconds = timeout_seconds

    async def verify(self, user_id: str | None) -> AdministratorVerdict:
        if user_id is None or not HA_USER_ID_PATTERN.fullmatch(user_id):
            return AdministratorVerdict.IDENTITY_MISSING
        token = SINGLE_READ.set(True)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._websocket_client.command(dict(AUTH_LIST_COMMAND))
        except Exception:
            return AdministratorVerdict.LOOKUP_FAILED
        finally:
            SINGLE_READ.reset(token)
        try:
            return administrator_verdict(result, user_id)
        except Exception:
            return AdministratorVerdict.LOOKUP_FAILED


class ApprovalIdentityCounters:
    """Process-lifetime refusal counts for ``get_server_health``. Counts only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._refusals: Counter = Counter()
        self._last_reason: str | None = None

    def record_refusal(self, verdict: AdministratorVerdict) -> None:
        with self._lock:
            self._refusals[verdict.value] += 1
            self._last_reason = verdict.value

    def reset(self) -> None:
        with self._lock:
            self._refusals.clear()
            self._last_reason = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            by_reason = {
                verdict.value: self._refusals[verdict.value]
                for verdict in REFUSAL_VERDICTS
            }
            return {
                "administrator_role_source": "home_assistant_core_config_auth_list_per_request",
                "refused_attempts": sum(by_reason.values()),
                "refused_by_reason": by_reason,
                "last_refusal_reason": self._last_reason,
                "counter_semantics": "cumulative_process_events",
            }


APPROVAL_IDENTITY_COUNTERS = ApprovalIdentityCounters()


def approval_identity_health() -> dict[str, Any]:
    return APPROVAL_IDENTITY_COUNTERS.snapshot()


__all__ = [
    "ADMIN_INGRESS_PRINCIPAL_PREFIX",
    "APPROVAL_IDENTITY_COUNTERS",
    "AUTH_LIST_COMMAND",
    "AdministratorVerdict",
    "AdministratorVerifier",
    "ApprovalIdentityCounters",
    "HA_USER_ID_PATTERN",
    "administrator_principal",
    "administrator_verdict",
    "approval_identity_health",
    "ingress_user_id",
]
