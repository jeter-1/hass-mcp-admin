"""Synthetic administrator verification for Ingress approval tests (HAMCP-205).

The production approval application refuses every page without a verifier.
Tests that exercise approval flows rather than role verification inject one of
these offline doubles; ``tests/test_hamcp205_admin_verification.py`` covers the
real verifier against a synthetic ``config/auth/list`` transport.
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "hass_mcp_engineering_beta") not in sys.path:
    sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.approval_identity import (  # noqa: E402
    AdministratorVerdict,
    HA_USER_ID_PATTERN,
)


class StaticAdministratorVerifier:
    """Answer from a fixed role table; every call is recorded, nothing cached."""

    def __init__(self, roles=None, *, default=AdministratorVerdict.ADMINISTRATOR):
        self.roles = dict(roles or {})
        self.default = default
        self.calls: list[str | None] = []

    async def verify(self, user_id):
        self.calls.append(user_id)
        if user_id is None or not HA_USER_ID_PATTERN.fullmatch(user_id):
            return AdministratorVerdict.IDENTITY_MISSING
        return self.roles.get(user_id, self.default)


def admin_verifier() -> StaticAdministratorVerifier:
    """Every well-formed forwarded user is an administrator."""

    return StaticAdministratorVerifier()
