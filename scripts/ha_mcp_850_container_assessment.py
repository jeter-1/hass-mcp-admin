"""CI-only exact-image assessment, without changing Engineering admission.

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
PINS = ROOT / "tests/fixtures/ha_mcp_850_assessment.json"
BRANCH = "refs/heads/assessment/ha-mcp-8.5.0-container-validation"
CORE_URL = "http://127.0.0.1:18123"
ENDPOINTS = {"standalone": "http://127.0.0.1:18086/assessment-mcp",
             "addon": "http://127.0.0.1:19583/assessment-mcp"}
MAX_BYTES = 4_000_000
FAN = "fan.hamcp_contract_fan"
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


def execution_guard(env, architecture):
    require(env.get("GITHUB_ACTIONS") == "true", "github_runner_required")
    require(env.get("GITHUB_REPOSITORY") == "jeter-1/hass-mcp-admin", "repository_mismatch")
    require(env.get("GITHUB_REF") == BRANCH, "task_branch_required")
    require(architecture in {"amd64", "arm64"}, "architecture_invalid")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[0-9]{1,16}", env.get(key, "")) is not None, "run_identity_invalid")
    return f"h850-{env['GITHUB_RUN_ID']}-{env['GITHUB_RUN_ATTEMPT']}-{architecture}"


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
    require(re.fullmatch(r"h850-[0-9]{1,16}-[0-9]{1,16}-(amd64|arm64)", identity) is not None,
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
    from ha_mcp_engineering.clients.upstream_read import McpReadCatalog
    from ha_mcp_engineering.providers.upstream_fan import FanProvider
    from ha_mcp_engineering.fan.contracts import FanRefusal
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from core_registry_contract_lane import configure_with_test_authority

    upstream_source = ROOT / ".upstream-850"
    source_sha = subprocess.check_output(["git", "-C", str(upstream_source), "rev-parse", "HEAD"], text=True).strip()
    require(source_sha == pins["upstream_source"], "upstream_source_mismatch")
    skills_sha = subprocess.check_output(["git", "-C", str(upstream_source / "src/ha_mcp/resources/skills-vendor"), "rev-parse", "HEAD"], text=True).strip()
    require(skills_sha == pins["skills_source"], "upstream_skills_mismatch")
    config = private / "core"
    config.mkdir()
    (config / "custom_components").mkdir()
    for source in (ROOT / "tests/fixtures/real_ha_device_migration/custom_components/beta23_device_fixture",
                   upstream_source / "custom_components/ha_mcp_tools"):
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
            await existing._advance_config_flow(client, token, "beta23_device_fixture", [{"slot": "a"}])
        original = {"title": "Assessment", "views": [{"title": "Original", "path": "test", "cards": []}]}
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
            if kind == "addon":
                # Activate only the tools component, never its embedded server.
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
                    await existing._advance_config_flow(client, token, "ha_mcp_tools", [{"next_step_id": "tools"}, {}])
            image = pins["images"][architecture][kind]
            docker("pull", "--platform", "linux/" + architecture, image["image"], timeout=300)
            image_id = docker("image", "inspect", "--format", "{{.Id}}", image["image"]).stdout.strip()
            require(image_id == image["configuration"], "image_configuration_mismatch")
            require(docker("image", "inspect", "--format", "{{.Architecture}}", image["image"]).stdout.strip() == architecture, "upstream_architecture_mismatch")
            labels = json.loads(docker("image", "inspect", "--format", "{{json .Config.Labels}}", image["image"]).stdout)
            require(labels.get("org.opencontainers.image.version" if kind == "standalone" else "io.hass.version") == "8.5.0", "image_version_mismatch")
            if kind == "standalone":
                require(labels.get("org.opencontainers.image.revision") == image["revision"], "image_revision_mismatch")
            env_file = private / (kind + ".env")
            lines = ["HA_MCP_DISABLE_UPDATE_CHECK=1", "HA_MCP_DISABLE_SETTINGS_UI=1", "MCP_HOST=0.0.0.0", "PYTHONDONTWRITEBYTECODE=1", "ENABLE_AUTO_BACKUP=false"]
            if kind == "standalone":
                lines += ["HOMEASSISTANT_URL=http://supervisor/core", "HOMEASSISTANT_TOKEN=" + token, "MCP_PORT=8086", "MCP_SECRET_PATH=/assessment-mcp", "ENABLE_TOOL_SEARCH=false"]
            else:
                lines += ["SUPERVISOR_TOKEN=" + token]
            with env_file.open("x") as stream:
                os.chmod(env_file, 0o600)
                stream.write("\n".join(lines) + "\n")
            mounts = ["--tmpfs", "/tmp", "--tmpfs", "/home/mcpuser/.ha-mcp"]
            if kind == "addon":
                data = private / "addon-data"
                data.mkdir()
                (data / "options.json").write_text(json.dumps({"secret_path": "/assessment-mcp", "enable_tool_search": False, "read_only_mode": False, "enable_mandatory_bps": True, "enable_strict_mandatory_bps": True, "enable_auto_backup": False, "enable_tool_security_policies": True}))
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
                    require(init.serverInfo.name == "ha-mcp" and init.serverInfo.version == "8.5.0" and init.protocolVersion == "2025-03-26", "initialized_identity_mismatch")
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
                        require(len(tools) == 77, "unexpected_catalog_count")
                        save(output, f"{kind}-catalog-{capture}.json", {"pages": pages, "tools": tools})
                        catalogs.append(tools)
                    require(catalogs[0] == catalogs[1], "catalog_changed_in_session")
                    names = {t["name"] for t in tools}
                    require("ha_manage_blueprints" in names and "ha_get_blueprint" not in names, "blueprint_catalog_contract")
                    authority_calls = []
                    try:
                        FanProvider(None, lambda: authority_calls.append(True)).validate_catalog(McpReadCatalog("2025-03-26", "ha-mcp", "8.5.0", tuple(tools), 0))
                    except FanRefusal:
                        require(not authority_calls, "unreviewed_authority_reached")
                    else:
                        raise Refusal("unreviewed_fan_admitted")
                    calls = []
                    async def call(name, arguments, success=True):
                        phase(kind + "_" + name)
                        require(len(calls) < 24, "tool_call_bound")
                        raw = checked_result(await session.call_tool(name, arguments))
                        calls.append({"tool": name, "arguments": arguments, "result": raw})
                        # Persist before interpretation; an uncertain write is never repeated.
                        save(output, f"{kind}-call-{len(calls):02d}.json", calls[-1])
                        if success is None:
                            return raw
                        if success:
                            return payload(raw, name)
                        structured = raw.get("structuredContent")
                        require(raw.get("isError") is True or isinstance(structured, dict) and structured.get("success") is False, "expected_tool_refusal")
                        return raw
                    state_read = await call("ha_get_state", {"entity_id": FAN})
                    require(state_read["data"]["state"] == "off", "initial_fan_not_off")
                    await call("ha_get_entity", {"entity_id": FAN})
                    await call("ha_list_services", {"domain": "fan", "limit": 10})
                    await call("ha_manage_blueprints", {"action": "list", "domain": "automation"})
                    await call("ha_manage_blueprints", {"action": "get", "domain": "automation", "path": "assessment/read.yaml"})
                    await call("ha_manage_blueprints", {"action": "invalid-assessment-action"}, success=False)
                    await call("ha_get_state", {"entity_id": "sensor.assessment_missing"}, success=False)
                    for action, percentage in (("turn_on", 66), ("turn_off", None)):
                        await call("ha_call_service", {"domain": "fan", "service": action, "entity_id": FAN, "data": {} if percentage is None else {"percentage": percentage}, "wait": False, "return_response": False, "verbose": False})
                        for _ in range(10):
                            actual = await rest.request("GET", "/states/" + FAN)
                            if actual["state"] == ("on" if action == "turn_on" else "off") and (percentage is None or actual["attributes"]["percentage"] == percentage):
                                break
                            await asyncio.sleep(1)
                        else:
                            raise Refusal("fan_independent_readback_failed")
                    getter = {"url_path": DASHBOARD, "force_reload": True, "include_screenshot": False}
                    before = await call("ha_config_get_dashboard", getter)
                    require(before["config"] == original, "dashboard_baseline_mismatch")
                    guide_raw = await call("ha_get_skill_guide", {"skill": "home-assistant-best-practices", "file": "references/dashboard-guide.md"}, success=None)
                    match = re.search(r"I-HAVE-READ-THE-BEST-PRACTICES-GUIDE-[0-9a-f]{8}", json.dumps(guide_raw))
                    require(match is not None, "bps_receipt_unavailable")
                    changed = json.loads(json.dumps(original))
                    changed["views"][0]["title"] = "Temporary " + kind
                    for desired in (changed, original):
                        await call("ha_config_set_dashboard", {"url_path": DASHBOARD, "config": desired, "config_hash": before["config_hash"], "MandatoryBPS": True, "BestPracticeKey": match.group(0), "return_screenshot": False})
                        independent = await websocket.command({"type": "lovelace/config", "url_path": DASHBOARD, "force": True})
                        require(independent == desired, "dashboard_independent_readback_failed")
                        before = await call("ha_config_get_dashboard", getter)
                        require(before["config"] == desired, "dashboard_upstream_readback_failed")
                    save(output, kind + "-result.json", {"status": "PASS", "catalog_count": len(tools), "repeated_catalog_equal": True, "engineering_850_admission": "REFUSED_AS_EXPECTED", "synthetic_fan_actions": 2, "fan_restored": "off", "dashboard_replacements": 2, "dashboard_restored": True, "component_configured": kind == "addon", "governed_850_execution": "NOT_IMPLEMENTED", "physical_device": False})
            docker("stop", "--time", "20", identity + "-" + kind, timeout=30)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:
            async with client.get("http://127.0.0.1:18080/_assessment/stats") as response:
                stats = await response.json()
        require(stats.get("service_posts", 0) == 4, "unexpected_fan_dispatch_count")
        require(stats.get("active_websockets", 0) == 0, "relay_sessions_retained")
        save(output, "relay-settlement.json", stats)
    finally:
        monitor = core._connection_monitor_task
        core.request_reconciliation(connection_changed=True)
        if monitor is not None:
            await asyncio.wait_for(asyncio.gather(monitor, return_exceptions=True), timeout=10)


def main():
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser()
    parser.add_argument("--relay", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--architecture", choices=("amd64", "arm64"))
    args = parser.parse_args()
    if args.relay:
        asyncio.run(run_relay())
        return
    identity = execution_guard(os.environ, args.architecture)
    if args.cleanup:
        print(json.dumps({"cleanup": cleanup(identity)}))
        return
    pins = json.loads(PINS.read_bytes())
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    output = runner / (identity + "-evidence")
    output.mkdir(mode=0o700)
    private = Path(tempfile.mkdtemp(prefix=identity + "-private-", dir=runner))
    receipt = {"started_at": now(), "status": "FAILED", "source": os.environ.get("GITHUB_SHA"), "base": pins["engineering_source"], "architecture": args.architecture, "production_access": False, "signing": "ephemeral_Core_test_key_only", "engineer_runtime_modified": False}
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
        receipt["finished_at"] = now()
        if failed:
            receipt["status"] = "FAIL"
        save(output, "receipt.json", receipt)
        print(json.dumps(receipt))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
