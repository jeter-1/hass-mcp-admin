"""CI-harness-only Home Assistant REST routes (HAMCP-200).

The disposable-Core harnesses under ``scripts/`` need four setup routes that
the Engineering runtime never requests: creating a config entry, firing the
contract event and reloading automations or scripts. They live here, outside
the add-on package, so the runtime cannot import them and its REST client
cannot be given one: the runtime ``Route`` check accepts only the runtime
``ROUTE_TEMPLATES``.

``harness_request`` sends exactly ``api_url + template`` (the URL the runtime
client sent for these routes before they moved) with the same headers, JSON
body and disabled redirects, and raises the same typed errors. It accepts only a template from
``HARNESS_ROUTE_TEMPLATES``, only a loopback (disposable) Core, and never
inside an Engineering request context.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
from yarl import URL

from ha_mcp_engineering.clients.rest import endpoint_category
from ha_mcp_engineering.errors import (
    HomeAssistantApiError,
    HomeAssistantTimeoutError,
    HomeAssistantUnavailableError,
)
from ha_mcp_engineering.request_context import current_telemetry


HARNESS_ROUTE_TEMPLATES = frozenset({
    "/config/config_entries/flow",
    "/events/dev14_real_contract_trigger",
    "/services/automation/reload",
    "/services/script/reload",
})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class HarnessRouteError(ValueError):
    """A harness request named no harness route or left it; nothing was sent."""


def harness_url(api_url: str, template: str) -> URL:
    """Return the exact disposable-Core URL for one harness-only route."""

    if type(template) is not str or template not in HARNESS_ROUTE_TEMPLATES:
        raise HarnessRouteError("not a CI-harness route")
    base = URL(api_url)
    if base.host not in LOOPBACK_HOSTS:
        raise HarnessRouteError("CI-harness routes go only to a loopback Core")
    url = URL(api_url.rstrip("/") + template)
    expected = tuple(part for part in base.parts if part not in ("", "/"))
    expected += tuple(template.split("/")[1:])
    if (
        (url.scheme, url.raw_host, url.port) != (base.scheme, base.raw_host, base.port)
        or url.user is not None
        or url.password is not None
        or url.query_string
        or url.raw_fragment
        or tuple(part for part in url.parts if part != "/") != expected
    ):
        raise HarnessRouteError("CI-harness route left its template")
    return url


async def harness_request(settings: Any, method: str, template: str, body: Any = None) -> Any:
    """Send one harness-only route to the disposable Core named by ``settings``."""

    url = harness_url(settings.api_url, template)
    if current_telemetry() is not None:
        raise HarnessRouteError("CI-harness routes never run inside an Engineering request")
    headers = {
        "Authorization": f"Bearer {settings.ha_token}",
        "Content-Type": "application/json",
    }
    category = endpoint_category(template)
    timeout = aiohttp.ClientTimeout(total=settings.ha_timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, url, headers=headers, json=body, allow_redirects=False
            ) as response:
                text = await response.text()
                if response.status >= 300:
                    raise HomeAssistantApiError(details={
                        "status": response.status,
                        "method": method,
                        "endpoint_category": category,
                        "provider_response_received": True,
                    })
                try:
                    return json.loads(text) if text else None
                except json.JSONDecodeError:
                    return text
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise HomeAssistantTimeoutError(
            details={"method": method, "endpoint_category": category}
        ) from exc
    except (aiohttp.ClientConnectionError, OSError) as exc:
        raise HomeAssistantUnavailableError(
            details={"method": method, "endpoint_category": category}
        ) from exc
