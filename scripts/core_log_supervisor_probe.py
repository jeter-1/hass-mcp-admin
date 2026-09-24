"""Disposable exact-Supervisor pytest probe; never run against an installation.

Copy this file into tests/api/test_engineering_core_logs.py of a disposable
Supervisor checkout at 40e3ee7640a3c44abe67f1a1397f39c3cd949806. Set
ENGINEERING_PROBE_REPO and ENGINEERING_PROBE_PYTHON to the Engineering candidate
and its Python 3.12 test environment. Supervisor pytest needs Python 3.14.
It uses upstream's mocked Docker/D-Bus/host fixtures and a random loopback port.
"""

import asyncio
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock

from aiohttp import ClientPayloadError, web
import pytest
import yaml

from supervisor.api import RestAPI
from supervisor.apps.validate import SCHEMA_APP_CONFIG
from supervisor.const import CoreState
from supervisor.host.const import LogFormat


CLIENT = r'''
import asyncio, json, os, sys
from ha_mcp_engineering.configuration import Settings
from ha_mcp_engineering.errors import GovernanceError
from ha_mcp_engineering.providers import core_logs
assert sys.argv[1].startswith("http://127.0.0.1:")
core_logs.SUPERVISOR_URL = sys.argv[1] + "/core/logs"
settings = Settings(ha_url="http://unused.invalid", ha_token="synthetic-unused-core",
    access_secret="synthetic-access-secret", port=8100, audit_path="unused.jsonl",
    rate_limit_per_minute=120, rate_limit_burst=20, destructive_services=frozenset(),
    ha_timeout_seconds=2.0)
async def main():
    try:
        data = await core_logs.CoreLogReader(settings).read_window(limit=20, offset=100)
        print(json.dumps({"success": True, "data": data}))
    except GovernanceError as exc:
        print(json.dumps({"success": False, "error_code": exc.code.value}))
asyncio.run(main())
'''


@pytest.mark.parametrize("mode", ["success", "missing_permission", "wrong_role", "late_error"])
async def test_engineering_core_log_reader(
    mode, coresys, install_app_example, aiohttp_client, journald_logs,
    journal_logs_reader, os_available, monkeypatch,
):
    """Cross the complete API route, security stack, app model, and log wrapper."""
    checkout = Path(__file__).resolve().parents[2]
    assert subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip() == "40e3ee7640a3c44abe67f1a1397f39c3cd949806"
    repo = Path(os.environ["ENGINEERING_PROBE_REPO"]).resolve()
    python = os.environ["ENGINEERING_PROBE_PYTHON"]
    manifest = yaml.safe_load((repo / "hass_mcp_engineering_beta/config.yaml").read_text())
    validated = SCHEMA_APP_CONFIG(manifest)
    assert validated["hassio_api"] is True
    assert validated["hassio_role"] == "homeassistant"
    install_app_example.persist["access_token"] = "synthetic-engineering-probe"
    install_app_example.data["hassio_api"] = mode != "missing_permission"
    install_app_example.data["hassio_role"] = (
        "default" if mode == "wrong_role" else validated["hassio_role"]
    )
    await coresys.core.set_state(CoreState.RUNNING)
    calls = []

    @web.middleware
    async def record(request, handler):
        calls.append((request.method, request.path))
        return await handler(request)

    async def lines():
        yield "synthetic-cursor", "2026-08-01 retained Core failure"
        yield None, "  traceback detail"
        yield None, "synthetic-engineering-probe"
        if mode == "late_error":
            raise ClientPayloadError("synthetic stream interruption")

    journal_logs_reader.return_value = lines()
    api = RestAPI(coresys)
    api.webapp.middlewares.insert(0, record)
    api.start = AsyncMock()
    await api.load()
    client = await aiohttp_client(api.webapp)
    assert client.host == "127.0.0.1"
    env = dict(os.environ, SUPERVISOR_TOKEN="synthetic-engineering-probe",
               PYTHONPATH=str(repo / "hass_mcp_engineering_beta"))
    child = await asyncio.create_subprocess_exec(
        python, "-c", CLIENT, f"http://127.0.0.1:{client.port}",
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, errors = await asyncio.wait_for(child.communicate(), timeout=15)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    assert child.returncode == 0, errors.decode()
    result = json.loads(output)
    assert calls == [("GET", "/core/logs")]
    if mode in {"missing_permission", "wrong_role"}:
        assert result == {"success": False, "error_code": "authorization_failure"}
        journald_logs.assert_not_called()
        return
    assert result["success"]
    data = result["data"]
    assert "2026-08-01 retained Core failure\n  traceback detail\n" in data["log"]
    assert "synthetic-engineering-probe" not in output.decode()
    assert data["provider"] == "supervisor_core_logs"
    assert data["window_completeness"] == "unknown"
    assert data["retention_coverage"] == "unknown"
    assert data["completeness"] == "partial"
    assert data["fallback_occurred"] is False
    journald_logs.assert_called_once_with(
        params={"SYSLOG_IDENTIFIER": "homeassistant"},
        range_header="entries=:-119:20", accept=LogFormat.JOURNAL,
    )
