"""HAMCP-155: the real Home Assistant clients' Core-authority check, for fakes.

``RestClient.request`` (``clients/rest.py``) and ``WebSocketClient.command``
(``clients/websocket.py``) refuse a call, before any I/O, when the request's
Core dispatch authorizer does not admit it.  A fake Home Assistant client that
skips that check lets a call made outside a Core-authority window succeed in
unit tests although the real client refuses it in production: the governed
delete's pre-dispatch drift read did exactly that.  Every governance/F3 fake
Home Assistant client or gateway calls ``require_core_authority`` before it
answers, so such a call fails in unit tests as it does against real Core.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.errors import (  # noqa: E402
    ErrorCode,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.request_context import current_telemetry  # noqa: E402


def require_core_authority(fake: Any, method: str, category: str) -> None:
    """Refuse exactly as the real client does; record the refused call.

    The refused call never reaches the fake's store or request log, as it
    never reaches Home Assistant.  It is appended to the fake's
    ``authority_refusals`` so a test can prove the refusal happened.
    """

    telemetry = current_telemetry()
    if telemetry is not None and not telemetry.authorize_core_dispatch():
        refusals = getattr(fake, "authority_refusals", None)
        if refusals is None:
            refusals = []
            fake.authority_refusals = refusals
        refusals.append((method, category))
        telemetry.error_code = ErrorCode.PROVIDER_UNAVAILABLE.value
        raise HomeAssistantUnavailableError(
            details={"method": method, "endpoint_category": category}
        )
