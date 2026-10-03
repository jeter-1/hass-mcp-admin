"""Prepared, unexecuted disposable Dashboard acceptance; never an installed probe.

Only --execute creates resources. --plan needs no daemon or Python dependencies.
Requires the reviewed candidate as an ancestor and an accessible local Docker
engine. No runtime version/authority changes, production targets, or credentials
are accepted. Exceptions are recorded by type only, never with provider text.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
import hashlib
import io
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import subprocess
import sys
import tarfile
import uuid

REVIEWED = "2791151b27424f686bc16c59f521ce5301517727"
CORE = "ghcr.io/home-assistant/home-assistant:2026.9.4@sha256:3e6710a7ab2a61311d9d899b719f6c3657791c63e8f4942cec4ebc42401d6b76"
MCP = "ghcr.io/homeassistant-ai/ha-mcp:8.5.0@sha256:e1538bcdadb13a5467bbb8258fba3262a05518585e18115742da638adec7057c"
IMAGE_IDS = {
    CORE: "sha256:cd74b0e02cee84de9f53b0f8fc079c8979796b81a6b02e1ee0cd09f038703afd",
    MCP: "sha256:e8dcb498378d91c9558698999f22461003c71aa67b405b9c17a76b792c85f4a7",
}
LABEL_KEY = "org.hass-mcp.dashboard-acceptance"
OBSERVER = "dashboard_interval_observer"
PATH = "dashboard-gate"
EMPTY_PATH = "dashboard-empty"
SENTINEL = "synthetic-dashboard-private-value"
DASHBOARD = {"title": SENTINEL, "views": [{"cards": [
    {"type": "entities", "entities": ["light.hamcp_contract_light",
        "light.hamcp_contract_light", "input_text.dashboard_private", "sensor.dashboard_absent"]},
    {"type": "custom:opaque-test-card", "private_payload": SENTINEL},
]}]}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def verify_mixed_projection(header, items):
    require(header["counts"]["reference_occurrences"] == 4 and header["counts"]["unique_references"] == 3,
            "occurrence accounting mismatch")
    # Inventory completeness proves absence for known literal references. The
    # unsupported custom card still makes overall semantic coverage partial.
    inventories = [row for row in header["sources"] if row["kind"] in {"states", "registry"}]
    require(len(inventories) == 2 and {row["kind"] for row in inventories} == {"states", "registry"}
            and all(row["complete"] is True and row["failure"] is None
                    and row["omitted"] == row["invalid"] == row["duplicate"] == 0 for row in inventories),
            "incomplete fixture inventories")
    require(header["coverage"] == {"references": "partial", "availability": "partial", "controls": "partial"},
            "unsupported custom card must retain partial coverage")
    references = [x for x in items if x["kind"] == "entity_reference"]
    require({x["pointer"] for x in references} == {"/views/0/cards/0/entities/" + str(i) for i in range(4)},
            "original pointer mismatch")
    require(any(x["entity_id"] == "sensor.dashboard_absent" and x["availability"] == "absent_from_observed_inventories"
                for x in references), "absence finding missing")
    require(any(x["kind"] == "control" for x in items) and any(x["kind"] == "coverage_gap" for x in items),
            "useful control/gap missing")


class Lane:
    def __init__(self, args):
        self.root, self.out = args.repo.resolve(), args.output.resolve()
        self.fixture = args.fixture.resolve()
        self.out.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.name = "dashboard-gate-" + uuid.uuid4().hex[:12]
        self.label = LABEL_KEY + "=" + self.name
        self.network, self.volume = self.name + "-net", self.name + "-config"
        self.core, self.upstream, self.seed = [self.name + x for x in ("-core", "-mcp", "-seed")]
        (self.out / "docker-client-config").mkdir(mode=0o700)
        self.prefix = ["docker", "--config", str(self.out / "docker-client-config"),
                       "--host", "unix:///var/run/docker.sock"]
        self.env = {k: v for k, v in os.environ.items()
                    if k.upper() not in {"DOCKER_HOST", "DOCKER_CONTEXT", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}}
        self.env.update(PYTHONDONTWRITEBYTECODE="1", GIT_OPTIONAL_LOCKS="0", NO_PROXY="127.0.0.1,localhost")
        self.attempted = False
        self.commands = []

    def docker(self, *args, timeout=60, input=None, token=None, checked=True):
        # argv has no token value. A disposable token is inherited by name only.
        self.commands.append(list(args))
        env = dict(self.env)
        if token:
            env["HOMEASSISTANT_TOKEN"] = token
        result = subprocess.run([*self.prefix, *args], env=env, input=input,
                                capture_output=True, timeout=timeout, check=False)
        if checked:
            require(result.returncode == 0, "owned Docker operation failed")
        return result

    def plan(self):
        require(platform.system() == "Linux" and platform.machine() == "x86_64", "only verified linux/amd64 is supported")
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, env=self.env).decode().strip()
        require(subprocess.run(["git", "merge-base", "--is-ancestor", REVIEWED, head],
                    cwd=self.root, env=self.env, capture_output=True).returncode == 0, "reviewed ancestor missing")
        require(not subprocess.check_output(["git", "status", "--porcelain=v1", "--untracked-files=no"],
                    cwd=self.root, env=self.env).strip(), "tracked candidate changes require a fresh binding")
        require((self.fixture / "__init__.py").is_file(), "observer source missing")
        source_hashes = {}
        for base in [self.root / "hass_mcp_engineering_beta/ha_mcp_engineering", self.fixture,
                     self.root / "tests/fixtures/real_ha_power/custom_components/power_contract_fixture"]:
            for file in sorted(base.rglob("*.py")):
                source_hashes[str(file.relative_to(base)) + "@" + base.name] = digest(file.read_bytes())
        self.head = head
        plan = {"status": "PREPARED_NOT_EXECUTED", "reviewed_ancestor": REVIEWED, "candidate_sha": head,
                "platform": "linux/amd64", "images": IMAGE_IDS, "engine": "unix:///var/run/docker.sock",
                "label": self.label, "containers": [self.seed, self.core, self.upstream],
                "network": self.network, "volume": self.volume, "bind_mounts": [],
                "ports": "unpublished; host reads exact owned internal-bridge addresses on 8123/8086", "privileged": False,
                "resource_limits": {"core": "3 GiB / 2 CPU / 512 pids", "mcp": "1 GiB / 1 CPU / 256 pids"},
                "timeouts_seconds": {"overall": 1200, "pull_each": 300, "onboarding": 150,
                    "setup_settle": 210, "each_analysis": 40, "observer_interval": 45, "stop_each": 20},
                "setup_writes": ["fresh configuration volume", "supported disposable admin onboarding",
                    "power fixture config flow", "two dashboard metadata rows", "one stored synthetic dashboard"],
                "analysis": "public registered Tool.run in process, real Core/MCP transports and actual signed test authority",
                "authority": "existing three profiles; ephemeral test-only registry key, never production signing",
                "reviewed_execution_argv_source": str(Path(__file__).resolve()),
                "execution_argv_source_sha256": digest(Path(__file__).read_bytes()),
                "source_hashes": source_hashes,
                "cleanup": [self.prefix + ["stop", "--time", "20", n] for n in (self.upstream, self.core, self.seed)]
                    + [self.prefix + ["rm", "--force", n] for n in (self.upstream, self.core, self.seed)]
                    + [self.prefix + ["volume", "rm", self.volume], self.prefix + ["network", "rm", self.network]],
                "cleanup_guard": "inspect exact resource label first; no prune or shared-image removal"}
        save(self.out / "plan.json", plan)
        return plan

    def start_core(self):
        self.attempted = True
        engine_version = self.docker("version", "--format", "{{.Server.Version}}", timeout=15).stdout.decode().strip()
        require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+_-]{0,40}", engine_version), "daemon identity unavailable")
        save(self.out / "engine-identity.json", {"version": engine_version, "platform": "linux/amd64"})
        for image, config in IMAGE_IDS.items():
            self.docker("pull", "--platform", "linux/amd64", image, timeout=300)
            actual = self.docker("image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", image).stdout.decode().strip()
            require(actual == config + " linux/amd64", "image configuration identity mismatch")
        self.docker("network", "create", "--internal", "--label", self.label, self.network)
        self.docker("volume", "create", "--label", self.label, self.volume)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            text = ("default_config:\nrecorder:\n  db_url: 'sqlite:///:memory:'\n"
                    "logger:\n  default: error\nlovelace:\n  mode: storage\n"
                    "input_text:\n  dashboard_private:\n    initial: " + SENTINEL + "\n" + OBSERVER + ":\n")
            raw = text.encode(); entry = tarfile.TarInfo("configuration.yaml"); entry.size = len(raw)
            archive.addfile(entry, io.BytesIO(raw))
            for directory in (self.fixture, self.root / "tests/fixtures/real_ha_power/custom_components/power_contract_fixture"):
                for file in sorted(directory.iterdir()):
                    if file.suffix in (".py", ".json"):
                        archive.add(file, arcname="custom_components/" + directory.name + "/" + file.name, recursive=False)
        self.docker("run", "--rm", "-i", "--name", self.seed, "--label", self.label,
                    "--network", "none", "--memory", "256m", "--cpus", "1", "--pids-limit", "64",
                    "--mount", "type=volume,src=" + self.volume + ",dst=/config", "--entrypoint", "tar",
                    CORE, "-xf", "-", "-C", "/config", input=buffer.getvalue())
        self.docker("run", "-d", "--name", self.core, "--label", self.label,
                    "--network", self.network, "--network-alias", "dashboard-core",
                    "--memory", "3g", "--cpus", "2", "--pids-limit", "512",
                    "--mount", "type=volume,src=" + self.volume + ",dst=/config", CORE)
        installed = json.loads(self.docker("exec", self.core, "python", "-c",
            "from importlib.metadata import version; import json; "
            "print(json.dumps({'core': version('homeassistant'), 'frontend': version('home-assistant-frontend')}))").stdout)
        require(installed == {"core": "2026.9.4", "frontend": "20260826.7"}, "installed component identity mismatch")
        save(self.out / "component-identities.json", {"installed_core_packages": installed,
             "verified_image_configuration_digests": IMAGE_IDS})
        return self.owned_url(self.core, 8123)

    def owned_url(self, name, port):
        # Internal bridges deliberately have no published host-port mapping.
        # Use the Linux host's route only after checking the exact owned network
        # and container; never fall back to another interface or caller URL.
        require((name, port) in {(self.core, 8123), (self.upstream, 8086)}, "unknown endpoint")
        network_format = ('{"internal":{{json .Internal}},"driver":{{json .Driver}},'
            '"id":{{json .Id}},"label":{{json (index .Labels "' + LABEL_KEY + '")}},'
            '"ipam":{{json .IPAM.Config}}}')
        container_format = ('{"running":{{json .State.Running}},'
            '"label":{{json (index .Config.Labels "' + LABEL_KEY + '")}},'
            '"network":{{json (index .NetworkSettings.Networks "' + self.network + '")}},'
            '"network_count":{{len .NetworkSettings.Networks}},"ports":{{json .HostConfig.PortBindings}}}')
        network = json.loads(self.docker("network", "inspect", "--format", network_format, self.network).stdout)
        container = json.loads(self.docker("container", "inspect", "--format", container_format, name).stdout)
        require(network["internal"] is True and network["driver"] == "bridge"
                and network["label"] == self.name, "unowned or external network")
        require(container["running"] is True and container["label"] == self.name
                and container["network_count"] == 1 and container["ports"] in (None, {}),
                "container topology mismatch")
        endpoint = container["network"]
        require(type(endpoint) is dict and endpoint.get("NetworkID") == network["id"], "network identity mismatch")
        address = ipaddress.ip_address(endpoint["IPAddress"])
        require(address.version == 4 and address.is_private and not address.is_loopback
                and not address.is_link_local and not address.is_unspecified and not address.is_multicast,
                "non-private container address")
        require(type(network["ipam"]) is list and 0 < len(network["ipam"]) <= 4, "invalid network allocation")
        require(any(address in ipaddress.ip_network(row["Subnet"])
                    and str(address) != row.get("Gateway") for row in network["ipam"]), "address outside owned network")
        save(self.out / ("endpoint-" + str(port) + ".json"), {
            "network": self.network, "container": name, "address": str(address), "port": port,
            "internal": True, "ownership_verified": True, "published_ports": False})
        return "http://" + str(address) + ":" + str(port)

    def start_upstream(self, token):
        self.docker("run", "-d", "--name", self.upstream, "--label", self.label,
                    "--network", self.network, "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
                    "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/home/mcpuser/.ha-mcp",
                    "-e", "HOMEASSISTANT_TOKEN",
                    "-e", "HOMEASSISTANT_URL=http://dashboard-core:8123", "-e", "MCP_HOST=0.0.0.0",
                    "-e", "MCP_PORT=8086", "-e", "MCP_SECRET_PATH=/disposable-dashboard-mcp",
                    "-e", "HA_MCP_DISABLE_UPDATE_CHECK=1", "-e", "HA_MCP_DISABLE_SETTINGS_UI=1",
                    "-e", "ENABLE_AUTO_BACKUP=false", "-e", "ENABLE_TOOL_SEARCH=false",
                    "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "MCP_HEALTHZ=true", MCP, "ha-mcp-web", token=token)
        return self.owned_url(self.upstream, 8086) + "/disposable-dashboard-mcp"

    def cleanup(self):
        result = {"status": "NOT_NEEDED" if not self.attempted else "PASS", "resources": []}
        if self.attempted:
            for kind, name in [("container", n) for n in (self.upstream, self.core, self.seed)] + [
                    ("volume", self.volume), ("network", self.network)]:
                fmt = '{{ index ' + ('.Config.Labels' if kind == 'container' else '.Labels') + ' "' + LABEL_KEY + '" }}'
                try:
                    seen = self.docker(kind, "ls", "--quiet", "--filter", "label=" + self.label, checked=True, timeout=5)
                    # Exact inspect failure means absent only if the working daemon's scoped list is empty.
                    info = self.docker(kind, "inspect", "--format", fmt, name, checked=False, timeout=5)
                    if info.returncode:
                        require(not seen.stdout.strip(), "resource inspect failed with owned resources remaining")
                        result["resources"].append({"name": name, "result": "absent"}); continue
                    require(info.stdout.decode().strip() == self.name, "cleanup label mismatch")
                    if kind == "container":
                        self.docker("stop", "--time", "20", name, checked=False, timeout=30)
                        self.docker("rm", "--force", name, timeout=15)
                    else:
                        self.docker(kind, "rm", name, timeout=15)
                    result["resources"].append({"name": name, "result": "removed"})
                except Exception as exc:
                    result["status"] = "FAIL"; result["resources"].append({"name": name, "error_type": type(exc).__name__})
            try:
                for kind in ("container", "volume", "network"):
                    args = [kind, "ls", "--quiet", "--filter", "label=" + self.label]
                    if kind == "container": args.append("--all")
                    require(not self.docker(*args, timeout=5).stdout.strip(), "owned resource remains")
            except Exception as exc:
                result["status"] = "FAIL"; result["final_verification_error"] = type(exc).__name__
        save(self.out / "cleanup.json", result)
        save(self.out / "docker-argv.json", self.commands)
        return result


@contextmanager
def observe_wire(events, core_url, mcp_url):
    """Transparent client dispatch observation: always call the real transports."""
    import aiohttp
    import httpx
    old_http = httpx.AsyncHTTPTransport.handle_async_request
    old_native = aiohttp.ClientRequest.send
    old_ws = aiohttp.ClientWebSocketResponse.send_json
    async def http(self, request):
        require(str(request.url) == mcp_url, "unexpected MCP endpoint")
        operation = "session_cleanup" if request.method == "DELETE" else "unexpected"
        if request.method == "POST":
            body = json.loads(request.content); operation = body.get("method")
            if operation == "tools/call":
                require(body["params"] == {"name": "ha_config_get_dashboard", "arguments": {
                    "url_path": events["path"], "force_reload": True, "list_only": False,
                    "include_screenshot": False}}, "selector drift")
        events["mcp"].append(operation)
        return await old_http(self, request)
    async def native(self, connection):
        from urllib.parse import urlsplit
        expected = urlsplit(core_url)
        require(self.url.host == expected.hostname and self.url.port == expected.port
                and self.url.path.startswith("/api/"), "unexpected native endpoint")
        events["native_http"].append(self.method + " " + self.url.path)
        return await old_native(self, connection)
    async def websocket(self, data, *args, **kwargs):
        events["native_ws"].append(data.get("type", "unknown"))
        return await old_ws(self, data, *args, **kwargs)
    httpx.AsyncHTTPTransport.handle_async_request = http
    aiohttp.ClientRequest.send = native
    aiohttp.ClientWebSocketResponse.send_json = websocket
    try:
        yield
    finally:
        httpx.AsyncHTTPTransport.handle_async_request = old_http
        aiohttp.ClientRequest.send = old_native
        aiohttp.ClientWebSocketResponse.send_json = old_ws


async def scenario(lane, core_url):
    sys.path[:0] = [str(lane.root / "scripts"), str(lane.root / "hass_mcp_engineering_beta")]
    import aiohttp
    import real_ha_contract_tests as helper
    from core_registry_contract_lane import configure_with_test_authority
    from alarmo_inspection_contract_acceptance import wait_disposable_setup
    from ha_mcp_engineering.clients import HomeAssistantWebSocketClient
    from ha_mcp_engineering.clients.mcp import MAX_TOOL_CATALOG_PAGES
    from ha_mcp_engineering.configuration import Settings
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.providers.upstream_dashboard import UpstreamDashboardProvider
    from ha_mcp_engineering.dashboard_analysis.runtime import DASHBOARD_ANALYSIS
    from ha_mcp_engineering.dashboard_analysis import contracts as c
    from ha_mcp_engineering.request_context import begin_request, end_request
    from ha_mcp_engineering.tools import dashboard as public

    helper.HA_URL, helper.CLIENT_ID = core_url, core_url + "/"
    async with asyncio.timeout(150):
        token = await helper.bootstrap_disposable_admin()
    mcp_url = lane.start_upstream(token)
    configured = Settings(ha_url=core_url, ha_token=token, access_secret=secrets.token_hex(32),
        port=8100, audit_path=str(lane.out / "audit.jsonl"), rate_limit_per_minute=120,
        rate_limit_burst=25, destructive_services=frozenset(), ha_timeout_seconds=30,
        upstream_dashboard_mcp_url=mcp_url, prewarm_enabled=False)
    ws = HomeAssistantWebSocketClient(configured)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        await helper._advance_config_flow(session, token, "power_contract_fixture", [{}])
    for _ in range(60):
        states = await ws.command({"type": "get_states"})
        if any(x["entity_id"] == "light.hamcp_contract_light" for x in states): break
        await asyncio.sleep(1)
    else: raise RuntimeError("fixture not ready")
    for path in (PATH, EMPTY_PATH):
        await ws.command({"type": "lovelace/dashboards/create", "url_path": path,
            "title": "Synthetic acceptance", "require_admin": True, "show_in_sidebar": False})
    await ws.command({"type": "lovelace/config/save", "url_path": PATH, "config": DASHBOARD})
    before = await ws.command({"type": "lovelace/config", "url_path": PATH, "force": True})
    require(before == DASHBOARD, "fixture configuration mismatch")
    core = CoreRuntime()
    try:
        await configure_with_test_authority(core, configured, cache_path=lane.out / "test-authority.json",
                                            expected_image=CORE, core_version="2026.9.4")
        upstream = UpstreamDashboardProvider(); upstream.configure(configured)
        # Readiness is setup only, not repeated admission or an analysis retry.
        from urllib.parse import urlsplit
        endpoint = urlsplit(mcp_url)
        for _ in range(60):
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(endpoint.hostname, endpoint.port), 1)
                writer.close(); await writer.wait_closed(); break
            except (OSError, TimeoutError): await asyncio.sleep(1)
        else: raise RuntimeError("upstream startup timeout")
        await upstream.refresh_capabilities()
        require(upstream.analysis_authority(), "exact admission unavailable")
        public.SETTINGS = configured
        DASHBOARD_ANALYSIS.configure(configured, core, upstream)
        service = DASHBOARD_ANALYSIS.require(); tool = public.registered_analysis_tool()
        # Same reviewed settle logic, fixed observer namespace supplied by a tiny delegate.
        class ObserverSetup:
            async def command(self, value):
                return await ws.command(dict(value, type=value["type"].replace("alarmo_interval_observer/", OBSERVER + "/")))
        async with asyncio.timeout(210): await wait_disposable_setup(ObserverSetup())
        receipts = []

        async def call(name, arguments, expected, native_reads=0, upstream_read=0):
            interval = (await ws.command({"type": OBSERVER + "/start", "kind": "inspection"}))["interval_id"]
            events = {"path": arguments.get("url_path", PATH), "mcp": [], "native_http": [], "native_ws": []}
            telemetry, context = begin_request(); telemetry.caller_id = "disposable-dashboard-owner"
            lease = core.acquire(c.REQUIREMENTS); commits = core.consume(lease) if lease else None
            telemetry.core_dispatch_authorizer = lambda: bool(lease and core.revalidate(lease, commits))
            try:
                with observe_wire(events, core_url, mcp_url):
                    async with asyncio.timeout(40): raw = await tool.run(arguments)
                value = json.loads(raw)
                require(value["success"] is expected, "unexpected analyzer outcome")
                require(len(raw.encode()) <= c.PAGE_BYTES and SENTINEL not in raw and token not in raw,
                        "output privacy or bound violation")
                require(value["timing"]["upstream_request_count"] == upstream_read, "upstream count mismatch")
                require(value["timing"]["home_assistant_request_count"] == native_reads, "native count mismatch")
                require(telemetry.upstream_active_requests == 0, "unbalanced upstream attempt")
                for field in ("upstream_ms", "upstream_wall_clock_span_ms"):
                    require(value["timing"][field] > 0 if upstream_read else value["timing"][field] == 0,
                            "upstream timing mismatch")
            finally:
                core.finish(commits); end_request(context)
                observed = await ws.command({"type": OBSERVER + "/finish", "interval_id": interval})
                save(lane.out / (name + "-observation.json"), observed)
                save(lane.out / (name + "-wire.json"), events)
            require(observed["overflow"] is False and observed["expired"] is False, "observer bound failed")
            require(observed["core_version"] == "2026.9.4" and all(observed["coverage"].values()), "observer unavailable")
            require(observed["observer_sha256"] == digest((lane.fixture / "__init__.py").read_bytes()), "observer identity drift")
            require(observed["baseline_store_hashes"] == observed["final_store_hashes"], "persisted fixture drift")
            commands = []
            for event in observed["events"]:
                if event["kind"] == "command": commands.append(event["command"])
                else:
                    require(event.get("origin") == "background" and event.get("store") in {"auth", "core.restore_state"}
                        and event["kind"] in {"storage_save", "storage_delay_save", "storage_write"}, "observed write/flow/service")
            require(commands == (["lovelace/config"] if upstream_read else []) +
                    (["config/entity_registry/list"] if native_reads else []), "unexpected Core dispatch or retry")
            if upstream_read:
                counts = Counter(events["mcp"])
                require(counts["tools/call"] == counts["initialize"] == counts["notifications/initialized"] == 1,
                        "upstream dispatch or initialization count mismatch")
                require(1 <= counts["tools/list"] <= MAX_TOOL_CATALOG_PAGES and counts["session_cleanup"] <= 1
                        and set(counts) <= {"tools/call", "initialize", "notifications/initialized", "tools/list", "session_cleanup"},
                        "unexpected upstream protocol dispatch")
            else: require(not events["mcp"], "unexpected upstream I/O")
            require(events["native_http"] == (["GET /api/states", "GET /api/websocket"] if native_reads else []), "native HTTP mismatch")
            require(events["native_ws"] == (["auth", "config/entity_registry/list"] if native_reads else []), "native WS mismatch")
            receipts.append({"case": name, "success": value["success"], "wire": events,
                "timing": value["timing"], "response_sha256": digest(raw.encode()), "bytes": len(raw.encode()),
                "refusal_reason": value.get("details", {}).get("reason")})
            save(lane.out / "case-receipts.json", receipts)
            return value

        first = await call("mixed", {"url_path": PATH, "limit": 1}, True, 2, 1)
        data = first["data"]; frozen = data["report_digest"]; first_cursor = data["pagination"]["next_cursor"]
        require(first_cursor, "fixture did not paginate")
        items = list(data["items"]); header = data["header"]
        for page in range(20):
            if not data["pagination"]["next_cursor"]: break
            value = await call("continuation-" + str(page), {"url_path": PATH, "limit": 100,
                "cursor": data["pagination"]["next_cursor"]}, True)
            data = value["data"]
            require(data["header"] == header and data["report_digest"] == frozen, "frozen projection drift")
            items.extend(data["items"])
        require(not data["pagination"]["next_cursor"], "export did not terminate")
        reconstructed = hashlib.sha256()
        for chunk in (c.canonical(header), *(c.canonical(item) for item in items)):
            reconstructed.update(len(chunk).to_bytes(8, "big")); reconstructed.update(chunk)
        require(frozen == "sha256:" + reconstructed.hexdigest(), "reconstructed report digest mismatch")
        save(lane.out / "projection.json", {"header": header, "items": items, "report_digest": frozen})
        verify_mixed_projection(header, items)
        size = len(service.snapshots)
        await call("invalid", {"url_path": PATH, "limit": 0}, False)
        await call("invalid-cursor", {"url_path": PATH, "cursor": "invalid"}, False)
        refused = await call("provider-refusal", {"url_path": EMPTY_PATH}, False, 0, 1)
        require(refused.get("details", {}).get("reason") == "source_rejected", "expected provider refusal absent")
        require(len(service.snapshots) == size, "refused collection retained snapshot")
        monitor = core._connection_monitor_task
        core.request_reconciliation(connection_changed=True)
        if monitor: await asyncio.wait_for(asyncio.gather(monitor, return_exceptions=True), 10)
        await call("retired-new", {"url_path": PATH}, False)
        await call("retired-continuation", {"url_path": PATH, "cursor": first_cursor}, False)
        after = await ws.command({"type": "lovelace/config", "url_path": PATH, "force": True})
        require(after == before, "dashboard mutated")
        states = await ws.command({"type": "get_states"})
        observed_controls = {x["entity_id"]: x["attributes"]["synthetic_service_calls"] for x in states
                             if x["entity_id"] in {"light.hamcp_contract_light", "switch.hamcp_contract_switch"}}
        require(observed_controls == {"light.hamcp_contract_light": 0, "switch.hamcp_contract_switch": 0},
                "control executed or fixture entity missing")
        save(lane.out / "acceptance.json", {"status": "PASS", "candidate_sha": lane.head,
            "images": IMAGE_IDS, "cases": len(receipts), "fixture_config_sha256": digest(canonical(before)),
            "config_unchanged": True, "controls_executed": 0, "snapshot_after_refusal_unchanged": True,
            "scope": "assembled disposable in-process public analyzer; not installed HTTP gateway or rendered frontend"})
    finally:
        monitor = core._connection_monitor_task
        core.request_reconciliation(connection_changed=True)
        if monitor: await asyncio.wait_for(asyncio.gather(monitor, return_exceptions=True), 10)


def failure_locations(error, root):
    result, current = [], error.__traceback__
    while current is not None and len(result) < 16:
        path = Path(current.tb_frame.f_code.co_filename).resolve()
        result.append({"file": str(path.relative_to(root)) if path.is_relative_to(root) else "dependency",
                       "line": current.tb_lineno})
        current = current.tb_next
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true"); mode.add_argument("--execute", action="store_true")
    mode.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.cleanup:
        # Recovery for CI cancellation: only the saved unique label, never prune.
        out = args.output.resolve()
        plan = json.loads((out / "plan.json").read_text())
        name = plan["label"].removeprefix(LABEL_KEY + "=")
        require(re.fullmatch(r"dashboard-gate-[0-9a-f]{12}", name), "invalid cleanup identity")
        lane = object.__new__(Lane)
        lane.out, lane.name, lane.label = out, name, LABEL_KEY + "=" + name
        lane.network, lane.volume = name + "-net", name + "-config"
        lane.core, lane.upstream, lane.seed = [name + x for x in ("-core", "-mcp", "-seed")]
        lane.prefix = ["docker", "--config", str(out / "docker-client-config"), "--host", "unix:///var/run/docker.sock"]
        lane.env, lane.attempted = dict(os.environ), True
        prior = out / "docker-argv.json"
        lane.commands = json.loads(prior.read_text()) if prior.exists() else []
        return 0 if lane.cleanup()["status"] == "PASS" else 1
    lane = Lane(args); lane.plan()
    if args.plan: print("Prepared plan only; no Docker or provider execution"); return 0
    logging.disable(logging.CRITICAL)
    os.environ.update(lane.env)
    for key in list(os.environ):
        if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}: os.environ.pop(key)
    def interrupt(_signum, _frame): raise RuntimeError("bounded execution interrupted")
    signal.signal(signal.SIGALRM, interrupt); signal.signal(signal.SIGTERM, interrupt); signal.alarm(1200)
    code = 1
    try:
        url = lane.start_core(); asyncio.run(scenario(lane, url)); code = 0
    except BaseException as exc:
        save(lane.out / "failure.json", {"status": "FAIL", "error_type": type(exc).__name__,
            "locations": failure_locations(exc, lane.root),
            "detail": "Exception values and locals omitted; source locations and completed receipts retained"})
    finally:
        signal.alarm(0)
        if lane.cleanup()["status"] == "FAIL": code = 1
        save(lane.out / "run-result.json", {"status": "PASS" if code == 0 else "FAIL",
            "candidate_sha": lane.head, "requires_both": ["acceptance.json", "cleanup.json"]})
    print("Disposable lane PASS" if code == 0 else "Disposable lane FAIL; see sanitized receipts")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
