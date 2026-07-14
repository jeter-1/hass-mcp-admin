"""Blocking contracts against one disposable Home Assistant Core instance.

This script is CI-only. It bootstraps a temporary administrator in the
throwaway container, never prints its credentials, and exercises the project
clients rather than the deployed Home Assistant environment.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import secrets
import sys

import aiohttp


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.clients import (  # noqa: E402
    HomeAssistantRestClient,
    HomeAssistantWebSocketClient,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.governance.normalize import normalize_automation  # noqa: E402
from ha_mcp_engineering.governance.service import AutomationGateway  # noqa: E402
from ha_mcp_engineering.trace_normalization import fetch_normalized_trace_list  # noqa: E402


HA_URL = os.environ.get("REAL_HA_URL", "http://127.0.0.1:8123").rstrip("/")
CLIENT_ID = f"{HA_URL}/"
AUTOMATION_ID = "beta25_contract_automation"


async def _json_request(session, method, path, *, json_body=None, data=None, token=""):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with session.request(
        method,
        f"{HA_URL}{path}",
        json=json_body,
        data=data,
        headers=headers,
    ) as response:
        if response.status >= 400:
            raise RuntimeError(f"Disposable Home Assistant bootstrap failed at {path} ({response.status})")
        return await response.json(content_type=None)


async def bootstrap_disposable_admin() -> str:
    """Complete first-run onboarding and return an in-memory access token."""

    username = f"contract_{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(32)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for _ in range(120):
            try:
                async with session.get(f"{HA_URL}/api/onboarding") as response:
                    if response.status == 200:
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(1)
        else:
            raise RuntimeError("Disposable Home Assistant did not become ready within 120 seconds")

        onboarding = await _json_request(session, "GET", "/api/onboarding")
        if any(item.get("done") for item in onboarding if item.get("step") == "user"):
            raise RuntimeError("Disposable Home Assistant configuration was not fresh")
        user = await _json_request(
            session,
            "POST",
            "/api/onboarding/users",
            json_body={
                "client_id": CLIENT_ID,
                "name": "Beta 25 Contract Administrator",
                "username": username,
                "password": password,
                "language": "en",
            },
        )
        auth_code = user.get("auth_code")
        if not auth_code:
            raise RuntimeError("Disposable Home Assistant did not return an onboarding authorization code")
        token = await _json_request(
            session,
            "POST",
            "/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": CLIENT_ID,
            },
        )
        access_token = token.get("access_token")
        if not access_token:
            raise RuntimeError("Disposable Home Assistant did not issue an access token")
        # Complete only supported onboarding steps; failures here are contract
        # failures because the temporary instance must represent a usable Core.
        steps = {item.get("step"): bool(item.get("done")) for item in onboarding}
        if not steps.get("core_config"):
            await _json_request(
                session,
                "POST",
                "/api/onboarding/core_config",
                json_body={},
                token=access_token,
            )
        if not steps.get("integration"):
            await _json_request(
                session,
                "POST",
                "/api/onboarding/integration",
                json_body={"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID},
                token=access_token,
            )
        if not steps.get("analytics"):
            await _json_request(
                session,
                "POST",
                "/api/onboarding/analytics",
                json_body={},
                token=access_token,
            )
        return access_token


def settings(token: str) -> Settings:
    return Settings(
        ha_url=HA_URL,
        ha_token=token,
        access_secret="disposable-contract-access-secret",
        port=8100,
        audit_path="/tmp/disposable-contract-audit.jsonl",
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        ha_timeout_seconds=30,
    )


async def run_contracts() -> None:
    token = await bootstrap_disposable_admin()
    configured = settings(token)
    rest = HomeAssistantRestClient(configured)
    websocket = HomeAssistantWebSocketClient(configured)
    gateway = AutomationGateway(rest)
    first = {
        "alias": "Beta 25 real HA contract",
        "description": "Created without top-level identity metadata",
        "trigger": [{"platform": "event", "event_type": "beta25_contract_event"}],
        "condition": [],
        "action": [{"event": "beta25_contract_observed", "event_data": {"source": "contract"}}],
        "mode": "single",
    }
    second = {**first, "description": "Updated again without top-level identity metadata"}
    try:
        await gateway.write(AUTOMATION_ID, first)
        read_first = await gateway.get(AUTOMATION_ID)
        assert read_first and read_first.get("id") == AUTOMATION_ID
        assert normalize_automation(read_first) == normalize_automation(first)

        await gateway.write(AUTOMATION_ID, second)
        read_second = await gateway.get(AUTOMATION_ID)
        assert read_second and read_second.get("id") == AUTOMATION_ID
        assert normalize_automation(read_second) == normalize_automation(second)
        assert read_second.get("alias") == first["alias"]
        assert read_second.get("mode") == "single"
        assert await gateway.get("beta25_contract_missing") is None

        validation = await gateway.validate()
        assert isinstance(validation, dict)
        assert not validation.get("errors")
        assert str(validation.get("result", "valid")).lower() in {"valid", "ok"}

        runtime_config = await rest.request("GET", "/config")
        assert isinstance(runtime_config, dict)
        assert runtime_config.get("version") == "2026.7.2"
        states = await rest.request("GET", "/states")
        assert isinstance(states, list)
        websocket_states = await websocket.command({"type": "get_states"})
        entity_registry = await websocket.command({"type": "config/entity_registry/list"})
        area_registry = await websocket.command({"type": "config/area_registry/list"})
        services = await websocket.command({"type": "get_services"})
        system_log = await websocket.command({"type": "system_log/list"})
        assert isinstance(entity_registry, list)
        assert isinstance(area_registry, list)
        assert isinstance(websocket_states, list)
        assert isinstance(services, dict)
        assert isinstance(system_log, list)
        assert services
        assert all(
            isinstance(item, dict) and "entity_id" in item and "state" in item
            for item in websocket_states
        )
        assert all(
            isinstance(item, dict) and "area_id" in item and "name" in item
            for item in area_registry
        )
        assert all(
            isinstance(item, dict) and "entity_id" in item and "platform" in item
            for item in entity_registry
        )
        assert all(
            isinstance(item, dict)
            and {"name", "message", "level", "timestamp"}.issubset(item)
            for item in system_log
        )

        # The trace contract is required, not silently skipped. Reloading and
        # firing this isolated event occur only in the disposable container.
        await rest.request("POST", "/services/automation/reload", {})
        await rest.request("POST", "/events/beta25_contract_event", {"source": "contract"})
        normalized = None
        for _ in range(30):
            await asyncio.sleep(1)
            normalized = await fetch_normalized_trace_list(
                websocket.command,
                AUTOMATION_ID,
                known_secrets=(token,),
            )
            if normalized.headers:
                break
        assert normalized is not None and normalized.headers
        trace = normalized.headers[0]
        run_id = trace.run_id
        assert run_id
        assert trace.started_at
        detail = await websocket.command(
            {"type": "trace/get", "domain": "automation", "item_id": AUTOMATION_ID, "run_id": run_id}
        )
        assert isinstance(detail, dict)
        assert isinstance(detail.get("trace"), dict)
        assert isinstance(detail.get("config"), dict)
    finally:
        # Deletion is best effort because the entire disposable container and
        # configuration directory are destroyed by the workflow trap.
        try:
            await rest.request("DELETE", f"/config/automation/config/{AUTOMATION_ID}")
        except Exception:
            pass


def main() -> int:
    try:
        asyncio.run(run_contracts())
    except Exception as exc:
        # Client exceptions are intentionally safe and exclude credentials and
        # response bodies. Never print token or onboarding values here.
        print(f"Real Home Assistant contract failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("Real Home Assistant 2026.7.2 contract assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
