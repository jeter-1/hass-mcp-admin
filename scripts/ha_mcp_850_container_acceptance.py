"""CI-only candidate acceptance with exact upstream images and disposable Core.

All targets, images and mutations are closed synthetic fixtures on a disposable
GitHub runner. No caller-supplied endpoints, credentials or image references.
The relay implements only the /core path needed by the actual add-on startup;
it is not Supervisor lifecycle or installed-system acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile

# The relay is mounted as /assessment.py; parent.parent is also valid there.
ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / "tests/fixtures/ha_mcp_850_candidate.json"
BRANCH = "refs/heads/main"
CORE_URL = "http://127.0.0.1:18123"
ENDPOINTS = {"standalone": "http://127.0.0.1:18086/synthetic-850/mcp",
             "addon": "http://127.0.0.1:19583/synthetic-850/mcp"}
MAX_BYTES = 4_000_000
FAN = "fan.hamcp_contract_fan"
POWER_TARGETS = ("light.hamcp_contract_light", "switch.hamcp_contract_switch")
DASHBOARD = "assessment-850"
LABEL = "io.hass-mcp.assessment"
PHASE = "preparation"


def phase(value):
    global PHASE
    PHASE = value
    print("Assessment phase: " + value, flush=True)


class Refusal(ValueError):
    """Fixed diagnostic categories only; no subprocess/HTTP exception text."""


def require(condition, category):
    if not condition:
        raise Refusal(category)


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode()


def save(folder, name, value):
    data = encode(value) + b"\n"
    require(len(data) <= MAX_BYTES, "evidence_bound")
    with (folder / name).open("xb") as stream:
        stream.write(data)


def version_code(version):
    require(version in {"8.4.3", "8.5.0"}, "upstream_version_invalid")
    return {"8.4.3": "843", "8.5.0": "850"}[version]


def execution_guard(env, architecture, version="8.5.0"):
    code = version_code(version)
    require(env.get("GITHUB_ACTIONS") == "true", "github_runner_required")
    require(env.get("GITHUB_REPOSITORY") == "jeter-1/hass-mcp-admin", "repository_mismatch")
    event, ref = env.get("GITHUB_EVENT_NAME"), env.get("GITHUB_REF", "")
    require(
        event == "pull_request" and re.fullmatch(r"refs/pull/[1-9][0-9]*/merge", ref)
        or event in {"push", "workflow_dispatch"} and ref == BRANCH,
        "candidate_ref_required",
    )
    require(env.get("GITHUB_JOB") == "exact-addon-runtime-acceptance", "candidate_job_required")
    require(re.fullmatch(r"[0-9a-f]{40}", env.get("GITHUB_SHA", "")) is not None,
            "candidate_sha_required")
    require(architecture in {"amd64", "arm64"}, "architecture_invalid")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[0-9]{1,16}", env.get(key, "")) is not None, "run_identity_invalid")
    return f"h{code}-{env['GITHUB_RUN_ID']}-{env['GITHUB_RUN_ATTEMPT']}-{architecture}"


def complete_catalog(pages):
    require(1 <= len(pages) <= 16, "catalog_page_bound")
    tools, cursors = [], set()
    for index, page in enumerate(pages):
        require(isinstance(page, dict) and isinstance(page.get("tools"), list), "catalog_malformed")
        tools.extend(page["tools"])
        require(len(tools) <= 512, "catalog_tool_bound")
        cursor = page.get("nextCursor")
        if index < len(pages) - 1:
            require(isinstance(cursor, str) and bool(cursor) and cursor not in cursors, "cursor_chain_invalid")
            cursors.add(cursor)
        else:
            require(cursor in (None, ""), "catalog_incomplete")
    names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
    require(len(names) == len(tools) and all(isinstance(n, str) and n for n in names), "catalog_name_invalid")
    require(len(set(names)) == len(names), "catalog_duplicate")
    require(len(encode(tools)) <= MAX_BYTES, "catalog_byte_bound")
    return tools


def checked_result(result):
    value = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    require(len(encode(value)) <= MAX_BYTES, "response_bound")
    return value


def payload(value, tool=None):
    require(not value.get("isError"), "upstream_tool_error")
    data = value.get("structuredContent")
    if not isinstance(data, dict):
        texts = [c["text"] for c in value.get("content", []) if c.get("type") == "text"]
        data = json.loads("\n".join(texts))
    require(isinstance(data, dict), "upstream_result_malformed")
    if tool == "ha_get_state":
        # This tool's actual 8.5.0 single-entity contract has data/metadata,
        # with no top-level success flag. Keep the assertion tool-specific.
        state = data.get("data")
        require(isinstance(state, dict) and state.get("entity_id") == FAN
                and isinstance(state.get("state"), str) and data.get("success") is not False,
                "upstream_state_malformed")
    else:
        require(data.get("success") is True, "upstream_result_unsuccessful")
    return data


def docker(*args, timeout=90, check=True):
    result = subprocess.run(["docker", "--host", "unix:///var/run/docker.sock", *args],
                            text=True, capture_output=True, timeout=timeout)
    if check:
        require(result.returncode == 0, "docker_command_failed")
    return result


def resource_names(identity):
    require(re.fullmatch(r"h(843|850)-[0-9]{1,16}-[0-9]{1,16}-(amd64|arm64)", identity) is not None,
            "cleanup_identity_invalid")
    return [identity + "-" + role for role in ("standalone", "addon", "relay", "core")]


def startup_diagnostics(identity):
    """Selected facts only. Never export logs or arbitrary exception text."""
    markers = {"Traceback (most recent call last)": "traceback_present",
               "PermissionError": "permission_error", "Read-only file system": "read_only_filesystem",
               "ModuleNotFoundError": "missing_module", "IndexError": "index_error",
               "Address already in use": "address_in_use", "exec format error": "wrong_executable_format"}
    result = []
    for name in resource_names(identity):
        owner = docker("inspect", "--format", '{{index .Config.Labels "' + LABEL + '"}}', name, check=False)
        if owner.returncode:
            continue
        require(owner.stdout.strip() == identity, "diagnostic_owner_mismatch")
        state = json.loads(docker("inspect", "--format", "{{json .State}}", name).stdout)
        logs = docker("logs", "--tail", "80", name, check=False)
        text = logs.stdout + logs.stderr
        result.append({"resource": name, "running": state["Running"], "exit_code": state["ExitCode"],
                       "oom_killed": state["OOMKilled"],
                       "known_markers": sorted(label for marker, label in markers.items() if marker in text)})
    return result


def cleanup(identity):
    results = []
    for name in resource_names(identity):
        found = docker("inspect", "--format", '{{index .Config.Labels "' + LABEL + '"}}', name, check=False)
        if found.returncode:
            # Distinguish absence from an unavailable daemon.
            docker("info", "--format", "{{.OSType}}", timeout=15)
            results.append({"resource": name, "status": "absent"})
            continue
        require(found.stdout.strip() == identity, "cleanup_owner_mismatch")
        docker("stop", "--time", "20", name, timeout=30)
        state = json.loads(docker("inspect", "--format", "{{json .State}}", name).stdout)
        docker("rm", name)
        results.append({"resource": name, "status": "removed", "exit_code": state["ExitCode"],
                        "oom_killed": state["OOMKilled"]})
    network = docker("network", "inspect", "--format", '{{index .Labels "' + LABEL + '"}}', identity, check=False)
    if network.returncode == 0:
        require(network.stdout.strip() == identity, "cleanup_network_owner_mismatch")
        docker("network", "rm", identity)
    docker("info", "--format", "{{.OSType}}", timeout=15)
    require(not docker("ps", "-aq", "--filter", f"label={LABEL}={identity}").stdout.strip(), "containers_retained")
    require(not docker("network", "ls", "-q", "--filter", f"label={LABEL}={identity}").stdout.strip(), "network_retained")
    return results


def relay_target(path, path_and_query):
    if path == "/core/websocket":
        return "http://core:8123/api/websocket"
    if path == "/core/api" or path.startswith("/core/api/"):
        return "http://core:8123" + path_and_query[len("/core"):]
    return None


def create_relay_app():
    """Synthetic /core forwarding inside an isolated, owned CI container."""
    import aiohttp
    from aiohttp import web
    stats = Counter()
    app = web.Application(client_max_size=MAX_BYTES)
    async def on_start(application):
        application["client"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    async def on_cleanup(application):
        await application["client"].close()
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_cleanup)
    async def forward(request):
        if request.path == "/_assessment/stats":
            return web.json_response(dict(stats))
        target = relay_target(request.path, request.rel_url.path_qs)
        if target is None:
            raise web.HTTPNotFound()
        if request.headers.get("Upgrade", "").lower() == "websocket":
            frontend = web.WebSocketResponse(max_msg_size=MAX_BYTES)
            async with app["client"].ws_connect(target, max_msg_size=MAX_BYTES) as backend:
                await frontend.prepare(request)
                stats["active_websockets"] += 1
                async def copy(source, destination, inbound):
                    async for message in source:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            if inbound:
                                value = json.loads(message.data)
                                kind = value.get("type")
                                if kind in {"lovelace/config/save", "ha_mcp_tools/dashboard_edit", "call_service"}:
                                    stats[kind] += 1
                            await destination.send_str(message.data)
                        elif message.type == aiohttp.WSMsgType.BINARY:
                            await destination.send_bytes(message.data)
                tasks = [asyncio.create_task(copy(frontend, backend, True)),
                         asyncio.create_task(copy(backend, frontend, False))]
                try:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await frontend.close()
                    stats["active_websockets"] -= 1
            return frontend
        if request.method == "POST" and request.path.startswith("/core/api/services/"):
            stats["service_posts"] += 1
            for domain in ("fan", "light", "switch"):
                if request.path.startswith("/core/api/services/" + domain + "/"):
                    stats["service_posts_" + domain] += 1
        headers = {key: request.headers[key] for key in ("Authorization", "Content-Type") if key in request.headers}
        async with app["client"].request(request.method, target, headers=headers, data=await request.read(), allow_redirects=False) as response:
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                require(len(data) <= MAX_BYTES, "relay_response_bound")
            return web.Response(status=response.status, body=bytes(data), headers={"Content-Type": response.headers.get("Content-Type", "application/json")})
    app.router.add_route("*", "/{path:.*}", forward)
    return app


async def run_relay():
    from aiohttp import web
    app = create_relay_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 80).start()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopped.set)
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        loop.remove_signal_handler(signal.SIGTERM)


async def wait_endpoint(url):
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as client:
        for _ in range(60):
            try:
                async with client.get(url) as response:
                    if response.status in {200, 400, 404, 405, 406}:
                        return
            except (aiohttp.ClientError, TimeoutError):
                pass
            await asyncio.sleep(1)
    raise Refusal("startup_timeout")


async def assess(architecture, output, private, identity, pins):
    phase("disposable_core_startup")
    import aiohttp
    from mcp import types
    from mcp.client.streamable_http import streamablehttp_client
    sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "hass_mcp_engineering_beta")]
    import real_ha_contract_tests as existing
    from ha_mcp_engineering.mcp_sdk_compatibility import ReviewedProtocolClientSession
    from ha_mcp_engineering.clients import HomeAssistantRestClient, HomeAssistantWebSocketClient
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from core_registry_contract_lane import configure_with_test_authority

    version = pins["version"]
    upstream_source = ROOT / (".upstream-" + version_code(version))
    source_sha = subprocess.check_output(["git", "-C", str(upstream_source), "rev-parse", "HEAD"], text=True).strip()
    require(source_sha == pins["upstream_source"], "upstream_source_mismatch")
    source_tree = subprocess.check_output(["git", "-C", str(upstream_source), "rev-parse", "HEAD^{tree}"], text=True).strip()
    require(source_tree == pins["upstream_tree"], "upstream_tree_mismatch")
    skills_sha = subprocess.check_output(["git", "-C", str(upstream_source / "src/ha_mcp/resources/skills-vendor"), "rev-parse", "HEAD"], text=True).strip()
    require(skills_sha == pins["skills_source"], "upstream_skills_mismatch")
    config = private / "core"
    config.mkdir()
    (config / "custom_components").mkdir()
    components = [ROOT / "tests/fixtures/real_ha_power/custom_components/power_contract_fixture"]
    if version == "8.5.0":
        components += [ROOT / "tests/fixtures/real_ha_device_migration/custom_components/beta23_device_fixture",
                       upstream_source / "custom_components/ha_mcp_tools"]
    for source in components:
        shutil.copytree(source, config / "custom_components" / source.name)
    (config / "configuration.yaml").write_text("default_config:\nautomation: !include automations.yaml\nscript: !include scripts.yaml\ninput_boolean: {}\ninput_number: {}\nkitchen_sink:\n")
    (config / "automations.yaml").write_text("[]\n")
    (config / "scripts.yaml").write_text("{}\n")
    bp = config / "blueprints/automation/assessment"
    bp.mkdir(parents=True)
    (bp / "read.yaml").write_text("blueprint:\n  name: Assessment Read Fixture\n  description: Synthetic offline fixture\n  domain: automation\n  input: {}\ntrigger: []\ncondition: []\naction: []\n")
    docker("network", "create", "--label", f"{LABEL}={identity}", identity)
    common = ["--network", identity, "--label", f"{LABEL}={identity}", "--security-opt", "no-new-privileges:true", "--pids-limit", "512"]
    docker("pull", "--platform", "linux/" + architecture, pins["core_image"], timeout=300)
    require(docker("image", "inspect", "--format", "{{.Architecture}}", pins["core_image"]).stdout.strip() == architecture, "core_architecture_mismatch")
    docker("run", "-d", "--name", identity + "-core", *common, "--network-alias", "core", "--memory", "3g", "-p", "127.0.0.1:18123:8123", "-v", str(config) + ":/config", pins["core_image"])
    existing.HA_URL, existing.CLIENT_ID = CORE_URL, CORE_URL + "/"
    token = await existing.bootstrap_disposable_admin()
    configured = existing.settings(token)
    rest, websocket = HomeAssistantRestClient(configured), HomeAssistantWebSocketClient(configured)
    core = CoreRuntime()
    try:
        observed = await existing.wait_for_runtime_ready(rest)
        require(observed["version"] == "2026.9.2", "core_version_mismatch")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
            if version == "8.5.0":
                await existing._advance_config_flow(client, token, "beta23_device_fixture", [{"slot": "a"}])
            await existing._advance_config_flow(client, token, "power_contract_fixture", [{}])
        original = {"title": "Assessment", "views": [{"title": "Original", "path": "test", "cards": []}]}
        if version == "8.5.0":
            await websocket.command({"type": "lovelace/config/save", "config": original})
            await websocket.command({"type": "lovelace/dashboards/create", "url_path": DASHBOARD, "title": "Assessment", "require_admin": True, "show_in_sidebar": False})
            await websocket.command({"type": "lovelace/config/save", "url_path": DASHBOARD, "config": original})
            for _ in range(30):
                try:
                    state = await rest.request("GET", "/states/" + FAN)
                    if state["state"] == "off":
                        break
                except Exception:
                    pass
                await asyncio.sleep(1)
            else:
                raise Refusal("synthetic_fan_unavailable")
        phase("core_authority")
        await configure_with_test_authority(core, configured, cache_path=private / "core-cache.json", expected_image=pins["core_image"])
        save(output, "core-authority.json", {"version": observed["version"], "compatible_count": core.health_snapshot()["compatible_count"], "ephemeral_test_authority": True, "production_authority": False})
        docker("run", "-d", "--name", identity + "-relay", *common, "--network-alias", "supervisor", "--memory", "256m", "--read-only", "--tmpfs", "/tmp", "-p", "127.0.0.1:18080:80", "-v", str(Path(__file__).resolve()) + ":/assessment.py:ro", "--entrypoint", "python", pins["core_image"], "/assessment.py", "--relay")
        await wait_endpoint("http://127.0.0.1:18080/_assessment/stats")
        for kind in ("standalone", "addon"):
            phase(kind + "_startup")
            if kind == "addon" and version == "8.5.0":
                # Activate only the tools component, never its embedded server.
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
                    await existing._advance_config_flow(client, token, "ha_mcp_tools", [{"next_step_id": "tools"}, {}])
            image = pins["images"][architecture][kind]
            docker("pull", "--platform", "linux/" + architecture, image["image"], timeout=300)
            image_id = docker("image", "inspect", "--format", "{{.Id}}", image["image"]).stdout.strip()
            require(image_id == image["configuration"], "image_configuration_mismatch")
            require(docker("image", "inspect", "--format", "{{.Architecture}}", image["image"]).stdout.strip() == architecture, "upstream_architecture_mismatch")
            labels = json.loads(docker("image", "inspect", "--format", "{{json .Config.Labels}}", image["image"]).stdout)
            require(labels.get("org.opencontainers.image.version" if kind == "standalone" else "io.hass.version") == version, "image_version_mismatch")
            if kind == "standalone":
                require(labels.get("org.opencontainers.image.revision") == image["revision"], "image_revision_mismatch")
            env_file = private / (kind + ".env")
            lines = ["HA_MCP_DISABLE_UPDATE_CHECK=1", "HA_MCP_DISABLE_SETTINGS_UI=1", "MCP_HOST=0.0.0.0", "PYTHONDONTWRITEBYTECODE=1", "ENABLE_AUTO_BACKUP=false"]
            if kind == "standalone":
                lines += ["HOMEASSISTANT_URL=http://supervisor/core", "HOMEASSISTANT_TOKEN=" + token, "MCP_PORT=8086", "MCP_SECRET_PATH=/synthetic-850/mcp", "ENABLE_TOOL_SEARCH=false"]
            else:
                lines += ["SUPERVISOR_TOKEN=" + token]
            with env_file.open("x") as stream:
                os.chmod(env_file, 0o600)
                stream.write("\n".join(lines) + "\n")
            mounts = ["--tmpfs", "/tmp", "--tmpfs", "/home/mcpuser/.ha-mcp"]
            if kind == "addon":
                data = private / "addon-data"
                data.mkdir()
                (data / "options.json").write_text(json.dumps({"secret_path": "/synthetic-850/mcp", "enable_tool_search": False, "read_only_mode": False, "enable_mandatory_bps": True, "enable_strict_mandatory_bps": True, "enable_auto_backup": False, "enable_tool_security_policies": True}))
                mounts += ["-v", str(data) + ":/data"]
            port = "127.0.0.1:18086:8086" if kind == "standalone" else "127.0.0.1:19583:9583"
            docker("run", "-d", "--name", identity + "-" + kind, *common, "--read-only", "--memory", "1g", *mounts, "--env-file", str(env_file), "-p", port, image["image"], *(["ha-mcp-web"] if kind == "standalone" else []))
            require(docker("inspect", "--format", "{{.Image}}", identity + "-" + kind).stdout.strip() == image_id, "running_image_mismatch")
            save(output, kind + "-image.json", {**image, "observed_configuration": image_id, "architecture": architecture, "default_addon_startup": kind == "addon"})
            await wait_endpoint(ENDPOINTS[kind])
            async with streamablehttp_client(ENDPOINTS[kind]) as (read, write, _):
                async with ReviewedProtocolClientSession(read, write, client_info=types.Implementation(name="isolated-850-assessment", version="1")) as session:
                    phase(kind + "_initialize_catalog")
                    init = await session.initialize()
                    require(init.serverInfo.name == "ha-mcp" and init.serverInfo.version == version and init.protocolVersion == "2025-03-26", "initialized_identity_mismatch")
                    save(output, kind + "-initialize.json", init.model_dump(mode="json", by_alias=True, exclude_none=True))
                    catalogs = []
                    for capture in (1, 2):
                        pages, cursor = [], None
                        for _ in range(16):
                            page = checked_result(await session.list_tools(cursor=cursor))
                            pages.append(page)
                            cursor = page.get("nextCursor")
                            if not cursor:
                                break
                        tools = complete_catalog(pages)
                        require(len(tools) == {"8.4.3": 78, "8.5.0": 77}[version], "unexpected_catalog_count")
                        save(output, f"{kind}-catalog-{capture}.json", {"pages": pages, "tools": tools})
                        catalogs.append(tools)
                    require(catalogs[0] == catalogs[1], "catalog_changed_in_session")
                    names = {t["name"] for t in tools}
                    require(("ha_manage_blueprints" in names and "ha_get_blueprint" not in names)
                            if version == "8.5.0" else ("ha_get_blueprint" in names and "ha_manage_blueprints" not in names),
                            "blueprint_catalog_contract")
            phase(kind + "_candidate_contract")
            if version == "8.5.0":
                await candidate_contract(kind, configured, core, rest, websocket,
                                         original, output, private)
            else:
                await power_only_candidate_contract(kind, configured, core, rest, output, private, version)
            docker("stop", "--time", "20", identity + "-" + kind, timeout=30)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:
            async with client.get("http://127.0.0.1:18080/_assessment/stats") as response:
                stats = await response.json()
        require(stats.get("service_posts", 0) == (14 if version == "8.5.0" else 8), "unexpected_total_dispatch_count")
        require(stats.get("service_posts_fan", 0) == (6 if version == "8.5.0" else 0), "unexpected_fan_dispatch_count")
        require(stats.get("service_posts_light", 0) == 4, "unexpected_light_dispatch_count")
        require(stats.get("service_posts_switch", 0) == 4, "unexpected_switch_dispatch_count")
        require(stats.get("lovelace/config/save", 0) == (2 if version == "8.5.0" else 0), "legacy_dashboard_dispatch_count")
        require(stats.get("ha_mcp_tools/dashboard_edit", 0) == (2 if version == "8.5.0" else 0), "native_dashboard_dispatch_count")
        require(stats.get("active_websockets", 0) == 0, "relay_sessions_retained")
        save(output, "relay-settlement.json", stats)
    finally:
        monitor = core._connection_monitor_task
        core.request_reconciliation(connection_changed=True)
        if monitor is not None:
            await asyncio.wait_for(asyncio.gather(monitor, return_exceptions=True), timeout=10)



async def candidate_contract(kind, configured, core, rest, websocket, original, output, private):
    """Actual candidate providers/executors; only synthetic targets owned by this lane."""
    from dataclasses import replace
    from uuid import uuid4
    from mcp.server.fastmcp import FastMCP
    from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
    from ha_mcp_engineering.providers.upstream_fan import FanProvider
    from ha_mcp_engineering.providers.upstream_dashboard import UpstreamDashboardProvider
    from ha_mcp_engineering.fan.contracts import FanRequest, FAN_RELEASES, digest
    from ha_mcp_engineering.fan.service import FanService
    from ha_mcp_engineering.fan.authority import FanCoreAuthority
    from ha_mcp_engineering.f3_dashboard.gateway import DashboardExecutionGateway
    from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
    from ha_mcp_engineering.governance.service import ChangeGovernanceService
    from ha_mcp_engineering.governance.resources import ConfigurationResourceGateway
    from ha_mcp_engineering.governance.storage import ChangePlanRepository
    from ha_mcp_engineering.audit import AuditLogger
    from ha_mcp_engineering.request_context import begin_request, end_request

    work = private / (kind + "-engineering")
    work.mkdir()
    configured = replace(configured, upstream_dashboard_mcp_url=ENDPOINTS[kind],
                         audit_path=str(work / "audit.jsonl"))
    gateway = UpstreamReadGateway()
    gateway.configure(configured, core_runtime=core)
    server = FastMCP("candidate-850-disposable")
    health = await gateway.initialize(server)
    require(health["admission_status"] == "admitted_exact", "candidate_read_admission")
    tools = gateway._registered_tool_registry.snapshot()
    require(len(tools) == 25 and "ha_get_blueprint" in tools
            and "ha_manage_blueprints" not in tools and "ha_call_service" not in tools,
            "candidate_public_read_projection")
    telemetry, context = begin_request("synthetic-850-" + kind)
    fan_service = None
    power_service = None
    try:
        for label, arguments in (("list", {}), ("get", {"path": "assessment/read.yaml"})):
            raw = await tools["ha_get_blueprint"].run(arguments)
            result = json.loads(raw)
            save(output, kind + "-candidate-blueprint-" + label + ".json", result)
            require(result.get("success") is True, "candidate_blueprint_failed")
            require(result["metadata"]["completeness"] ==
                    ("partial" if kind == "standalone" and label == "get" else "complete"),
                    "candidate_blueprint_completeness")
        denied = json.loads(await tools["ha_get_blueprint"].run({"action": "delete"}))
        require(denied.get("success") is not True
                and denied["metadata"]["upstream_dispatch_occurred"] is False,
                "candidate_blueprint_write_reachable")
        missing = json.loads(await tools["ha_get_state"].run({"entity_id": "sensor.synthetic_missing"}))
        require(missing.get("success") is False, "candidate_missing_state_not_refused")
        missing_automation = json.loads(await tools["ha_config_get_automation"].run(
            {"identifier": "synthetic_missing_automation"}))
        save(output, kind + "-candidate-missing-automation.json", missing_automation)
        require(missing_automation.get("success") is False
                and missing_automation.get("details", {}).get("failure_category") == "automation_not_found"
                and missing_automation["metadata"]["fallback_occurred"] is False,
                "candidate_empty_registry_automation_error")
        good = json.loads(await tools["ha_get_state"].run({"entity_id": FAN}))
        require(good.get("success") is True, "candidate_valid_read_after_error")

        fan_service = FanService(str(work / "fan"), FanProvider.configured(configured, gateway),
                                 FanCoreAuthority(core))
        for number, (action, percentage) in enumerate(
                (("turn_on", 50), ("set_percentage", 75), ("turn_off", None)), 1):
            request = FanRequest(entity_id=FAN, action=action, percentage=percentage,
                operation_id=f"{int(datetime.now(timezone.utc).timestamp())}-{uuid4().hex}")
            telemetry.ordinary_fan_binding = digest(request.model_dump())
            receipt = await fan_service.control(request)
            save(output, f"{kind}-candidate-fan-{number}-initial.json", receipt)
            for _ in range(6):
                if receipt["terminal"]:
                    break
                await asyncio.sleep(2)
                receipt = await fan_service.reconcile(request.task_id)
            save(output, f"{kind}-candidate-fan-{number}-terminal.json", receipt)
            require(receipt["state"] == "succeeded_verified"
                    and receipt["provider_attempt_count"] == 1
                    and receipt["dispatch_intent_recorded"] is True
                    and receipt["provider_contract"] == FAN_RELEASES["8.5.0"][2],
                    "candidate_fan_not_verified")
            require(await fan_service.control(request) == receipt, "candidate_duplicate_fan_changed")
            independent = await rest.request("GET", "/states/" + FAN)
            require(independent["state"] == ("off" if action == "turn_off" else "on")
                    and (percentage is None or independent["attributes"]["percentage"] == percentage),
                    "candidate_fan_independent_readback")
        require(not fan_service.locks.records() and fan_service.health()["nonterminal_tasks"] == 0
                and fan_service.health()["fallback_count"] == 0, "candidate_fan_not_settled")

        from ha_mcp_engineering.power.service import PowerService, PowerCoreAuthority
        from ha_mcp_engineering.providers.upstream_power import PowerProvider
        power_service = PowerService(str(work / "fan"), PowerProvider.configured(configured, gateway),
                                     PowerCoreAuthority(core), audit=AuditLogger(configured.audit_path, "synthetic-850-access"))
        power_summary = await power_contract(power_service, rest, telemetry, kind, output)

        provider = UpstreamDashboardProvider()
        provider.configure(configured)
        dashboard = DashboardExecutionGateway(provider, response_limit=60000)
        config_gateway = ConfigurationResourceGateway(rest, websocket)
        async def forbidden_lifecycle_identity():
            raise Refusal("unrelated_lifecycle_provider_reached")
        service = ChangeGovernanceService(
            ChangePlanRepository(work / "plans"), config_gateway,
            AuditLogger(str(work / "audit.jsonl"), "synthetic-850-access"),
            dashboard_gateway=dashboard, provider_identity_reader=forbidden_lifecycle_identity)
        runtime = F3RuntimeIntegration(
            service=service, storage_root=str(work / "plans"),
            configuration_gateway=config_gateway, backup_gateway=None, lifecycle_gateway=None,
            dashboard_gateway=dashboard, provider_identity_reader=forbidden_lifecycle_identity,
            retention_days=90, core_runtime=core)
        service.f3_runtime = runtime
        await runtime.recover_once("startup")
        baseline = await dashboard.preread(url_path=DASHBOARD)
        require(baseline.configuration == original, "candidate_dashboard_baseline")
        for number, title in enumerate(("Temporary " + kind, original["title"]), 1):
            created = await service.create_dashboard_update_plan(
                title="Disposable compatibility title update",
                description="One title leaf in an isolated Core fixture; restore exactly.",
                url_path=DASHBOARD,
                patch_operations=[{"operation": "replace", "operation_id": "title",
                                   "path": "/title", "value": title}],
                expiration_minutes=30)
            pending = service.approve(created["plan_id"], created["plan_hash"])
            review, csrf = await service.issue_external_csrf(created["plan_id"], pending["challenge_id"])
            require(review["dashboard_review"]["approval_projection"]["complete"] is True,
                    "candidate_approval_disclosure_incomplete")
            await service.decide_external_approval(
                plan_id=created["plan_id"], challenge_id=pending["challenge_id"],
                expected_plan_hash=created["plan_hash"], approval_kind=pending["approval_kind"],
                approval_action=pending["approval_action"], csrf_nonce=csrf, decision="approve",
                approver_principal="home_assistant_admin_ingress:synthetic-ci")
            applied = await service.apply(created["plan_id"], created["plan_hash"])
            save(output, f"{kind}-candidate-dashboard-{number}-apply.json", applied)
            task = service.get_execution_task(applied["task_id"])
            for _ in range(12):
                if task.get("state") == "succeeded_verified":
                    break
                await asyncio.sleep(1)
                await runtime.recover_once("ci_readback")
                task = service.get_execution_task(applied["task_id"])
            save(output, f"{kind}-candidate-dashboard-{number}-task.json", task)
            require(task.get("state") == "succeeded_verified", "candidate_dashboard_not_verified")
            duplicate = await service.apply(created["plan_id"], created["plan_hash"])
            require(duplicate["task_id"] == applied["task_id"], "candidate_duplicate_dashboard_changed")
            independent = await websocket.command(
                {"type": "lovelace/config", "url_path": DASHBOARD, "force": True})
            require(independent == {**original, "title": title}, "candidate_dashboard_independent_readback")
        restored = await dashboard.preread(url_path=DASHBOARD)
        require(restored.configuration == baseline.configuration
                and restored.config_hash == baseline.config_hash, "candidate_dashboard_not_restored")
        settled = runtime.health()
        require(all(settled[k] == 0 for k in (
            "nonterminal_execution_count", "active_conflict_hold_count",
            "active_normal_lock_count", "fallback_count")), "candidate_dashboard_not_settled")
        core_health = core.health_snapshot()
        require(core_health["compatible_count"] == 17
                and core_health["issued_lease_count"] == 0
                and core_health["active_commit_count"] == 0
                and core_health["fallback_count"] == 0, "candidate_core_not_settled")
        save(output, kind + "-candidate-result.json", {
            "status": "PASS", "engineering_source": os.environ["GITHUB_SHA"],
            "read_count": 25, "blueprint_completeness": "partial" if kind == "standalone" else "complete",
            "fan_operations": 3, "fan_restored": "off", "dashboard_operations": 2,
            "dashboard_restored": True, "component_configured": kind == "addon",
            "f3": settled, "fan": fan_service.health(), "power": power_summary,
            "core": {k: core_health[k] for k in (
                "compatible_count", "issued_lease_count", "active_commit_count", "fallback_count")},
            "physical_feedback": False, "approval": "synthetic_authenticated_principal"})
    finally:
        telemetry.ordinary_fan_binding = None
        telemetry.ordinary_power_binding = None
        if power_service is not None:
            await power_service.close()
        if fan_service is not None:
            await fan_service.close()
        if gateway._transport is not None:
            await gateway._transport.aclose()
        end_request(context)

async def power_only_candidate_contract(kind, configured, core, rest, output, private, version):
    """Close the historical provider gap without replacing older contract lanes."""
    from dataclasses import replace
    from mcp.server.fastmcp import FastMCP
    from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
    from ha_mcp_engineering.providers.upstream_power import PowerProvider
    from ha_mcp_engineering.power.service import PowerService, PowerCoreAuthority
    from ha_mcp_engineering.audit import AuditLogger
    from ha_mcp_engineering.request_context import begin_request, end_request

    require(version == "8.4.3", "power_only_version_invalid")
    work = private / (kind + "-engineering")
    work.mkdir()
    configured = replace(configured, upstream_dashboard_mcp_url=ENDPOINTS[kind],
                         audit_path=str(work / "audit.jsonl"))
    gateway = UpstreamReadGateway()
    gateway.configure(configured, core_runtime=core)
    telemetry, context = begin_request("synthetic-843-" + kind)
    service = None
    try:
        health = await gateway.initialize(FastMCP("candidate-843-disposable"))
        require(health["admission_status"] == "admitted_exact", "candidate_read_admission")
        tools = gateway._registered_tool_registry.snapshot()
        require(len(tools) == 25 and "ha_call_service" not in tools,
                "candidate_public_read_projection")
        service = PowerService(str(work / "power"), PowerProvider.configured(configured, gateway),
                               PowerCoreAuthority(core),
                               audit=AuditLogger(configured.audit_path, "synthetic-843-access"))
        summary = await power_contract(service, rest, telemetry, kind, output, version)
        state = core.health_snapshot()
        require(state["compatible_count"] == 17 and all(state[k] == 0 for k in
                ("issued_lease_count", "active_commit_count", "fallback_count")), "candidate_core_not_settled")
        save(output, kind + "-candidate-result.json", {
            "status": "PASS", "engineering_source": os.environ["GITHUB_SHA"],
            "upstream_version": version, "read_count": 25, "power": summary,
            "core": {k: state[k] for k in
                ("compatible_count", "issued_lease_count", "active_commit_count", "fallback_count")},
            "physical_feedback": False, "approval": "synthetic_authenticated_connector",
            "scope": "power_only_historical_fan_dashboard_lanes_preserved"})
    finally:
        telemetry.ordinary_power_binding = None
        if service is not None:
            await service.close()
        if gateway._transport is not None:
            await gateway._transport.aclose()
        end_request(context)


async def power_contract(service, rest, telemetry, kind, output, version="8.5.0"):
    """Exercise only the two fixed in-memory entities; read actual call counters."""
    version_code(version)
    from uuid import uuid4
    from ha_mcp_engineering.power.contracts import PowerRequest, POWER_RELEASES, digest
    receipts = []
    for entity in POWER_TARGETS:
        initial = await rest.request("GET", "/states/" + entity)
        require(initial["state"] == "off", "power_fixture_not_off")
        calls = initial["attributes"]["synthetic_service_calls"]
        for action in ("turn_on", "turn_off"):
            request = PowerRequest(entity_id=entity, action=action,
                operation_id=f"{int(datetime.now(timezone.utc).timestamp())}-{uuid4().hex}")
            telemetry.ordinary_power_binding = digest(request.model_dump())
            receipt = await service.control(request)
            save(output, f"{kind}-candidate-power-{len(receipts)+1}-initial.json", receipt)
            for _ in range(6):
                if receipt["terminal"]:
                    break
                await asyncio.sleep(2)
                receipt = await service.reconcile(request.task_id)
            save(output, f"{kind}-candidate-power-{len(receipts)+1}-terminal.json", receipt)
            require(receipt["state"] == "succeeded_verified"
                    and receipt["provider_attempt_count"] == 1
                    and receipt["dispatch_intent_recorded"] is True
                    and receipt["provider"] == "upstream_typed_power"
                    and receipt["provider_contract"] == POWER_RELEASES[version][2], "candidate_power_not_verified")
            require(await service.control(request) == receipt, "candidate_duplicate_power_changed")
            readback = await rest.request("GET", "/states/" + entity)
            calls += 1
            require(readback["state"] == ("on" if action == "turn_on" else "off")
                    and readback["attributes"]["synthetic_service_calls"] == calls,
                    "candidate_power_dispatch_or_readback_mismatch")
            receipts.append(receipt)
    require(not service.locks.records() and service.health()["nonterminal_tasks"] == 0
            and service.health()["fallback_count"] == 0
            and service.health()["audit_projection_failures"] == 0, "candidate_power_not_settled")
    return {"operations": len(receipts), "restored": "off", "health": service.health(),
            "physical_feedback_verified": False}


def main():
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser()
    parser.add_argument("--relay", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--upstream-version", choices=("8.4.3", "8.5.0"), default="8.5.0")
    parser.add_argument("--architecture", choices=("amd64", "arm64"))
    args = parser.parse_args()
    if args.relay:
        asyncio.run(run_relay())
        return
    identity = execution_guard(os.environ, args.architecture, args.upstream_version)
    if args.cleanup:
        print(json.dumps({"cleanup": cleanup(identity)}))
        return
    pin_path = PINS if args.upstream_version == "8.5.0" else ROOT / "tests/fixtures/ha_mcp_843_power_candidate.json"
    pins = json.loads(pin_path.read_bytes())
    require(pins["version"] == args.upstream_version, "pin_version_mismatch")
    checked_sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    require(checked_sha == os.environ["GITHUB_SHA"], "candidate_checkout_mismatch")
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    output = runner / (identity + "-evidence")
    output.mkdir(mode=0o700)
    private = Path(tempfile.mkdtemp(prefix=identity + "-private-", dir=runner))
    receipt = {"started_at": now(), "status": "FAILED", "source": os.environ.get("GITHUB_SHA"), "assessment_base": pins["assessment_source"], "architecture": args.architecture, "upstream_version": args.upstream_version, "production_access": False, "signing": "ephemeral_Core_test_key_only", "scope": "candidate_runtime_with_disposable_containers"}
    failed = False
    try:
        async def bounded():
            async with asyncio.timeout(900):
                await assess(args.architecture, output, private, identity, pins)
        asyncio.run(bounded())
        receipt["status"] = "PASS"
    except BaseException as error:
        failed = True
        receipt["failure_category"] = str(error) if isinstance(error, Refusal) else type(error).__name__
        receipt["phase"] = PHASE
    finally:
        if failed:
            try:
                receipt["diagnostics"] = startup_diagnostics(identity)
            except BaseException:
                receipt["diagnostics_unavailable"] = True
        try:
            receipt["cleanup"] = cleanup(identity)
        except BaseException as error:
            failed = True
            receipt["cleanup_failure"] = str(error) if isinstance(error, Refusal) else type(error).__name__
        # Root-owned Core files are removed by the job's fixed runner-temp cleanup.
        for name in ("standalone.env", "addon.env"):
            (private / name).unlink(missing_ok=True)
        receipt["completed_candidate_variants"] = [kind for kind in ("standalone", "addon")
            if (output / (kind + "-candidate-result.json")).is_file()]
        receipt["finished_at"] = now()
        if failed:
            receipt["status"] = "FAIL"
        save(output, "receipt.json", receipt)
        print(json.dumps(receipt))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
