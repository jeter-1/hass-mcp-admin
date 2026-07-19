"""Disposable exact-image RC2dev8 dependency-index failure bake.

This script creates its own Docker network, Home Assistant Core instance, and
Engineering server container. It accepts no Home Assistant or MCP endpoint and
cannot target a deployed installation. Synthetic credentials remain in process
memory/environment and are never included in the evidence artifact.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any

import aiohttp


ENGINEERING_IMAGE = (
    "ghcr.io/jeter-1/hass-mcp-engineering-beta@"
    "sha256:e1c2edf06f03e12ca42e1c90f43aa5c9e5b226b17acb69d302c1f483ff789a4a"
)
ENGINEERING_VERSION = "2.0.0-rc2-dev8"
ENGINEERING_REVISION = "c146c4378a221a34d66ee465772ecac09aca4899"
ENGINEERING_CREATED = "2026-07-19T13:14:16Z"
HOME_ASSISTANT_IMAGE = (
    "ghcr.io/home-assistant/home-assistant:2026.7.2@"
    "sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b"
)
HOME_ASSISTANT_VERSION = "2026.7.2"
SOFT_TTL_SECONDS = 5.0
HARD_TTL_SECONDS = 30.0
TARGET_ENTITY = "input_boolean.rc2dev8_exact_image_mode"
RESOURCE_LABEL = "io.hass-mcp.test=rc2dev8-exact-image"


class BakeFailure(RuntimeError):
    """A bounded failure that never contains subprocess or endpoint text."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acknowledge-disposable-exact-image",
        action="store_true",
        help="Required confirmation that the script may create isolated Docker resources.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("rc2dev8-exact-image-evidence.json"),
        help="Sanitized JSON evidence path.",
    )
    return parser.parse_args(argv)


def docker(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 180) -> str:
    try:
        completed = subprocess.run(
            ["docker", *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BakeFailure("A disposable Docker operation could not complete.") from None
    if completed.returncode:
        raise BakeFailure("A disposable Docker operation failed.")
    return completed.stdout.strip()


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def write_fixture_configuration(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "configuration.yaml").write_text(
        "\n".join(
            (
                "default_config:",
                "automation: !include automations.yaml",
                "input_boolean:",
                "  rc2dev8_exact_image_mode:",
                "    name: RC2dev8 exact-image mode",
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / "automations.yaml").write_text(
        "\n".join(
            (
                "- id: rc2dev8_exact_image_fixture",
                "  alias: RC2dev8 exact-image fixture",
                "  triggers:",
                "    - trigger: state",
                f"      entity_id: {TARGET_ENTITY}",
                "  conditions: []",
                "  actions: []",
                "  mode: single",
                "",
            )
        ),
        encoding="utf-8",
    )


def write_engineering_options(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    options = {
        "audit_enabled": True,
        "audit_max_payload_chars": 8192,
        "redaction_enabled": True,
        "prewarm_enabled": False,
        "prewarm_startup_delay_seconds": 45,
        "prewarm_retry_delay_seconds": 300,
        "dependency_index_soft_ttl_seconds": SOFT_TTL_SECONDS,
        "dependency_index_hard_ttl_seconds": HARD_TTL_SECONDS,
        "ha_timeout_seconds": 3,
        "response_size_limit": 60000,
        "log_level": "WARNING",
    }
    (root / "options.json").write_text(
        json.dumps(options, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


async def json_request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    body: Any = None,
    form: dict[str, str] | None = None,
    token: str = "",
) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with session.request(
        method,
        url,
        json=body,
        data=form,
        headers=headers,
    ) as response:
        if response.status >= 400:
            raise BakeFailure("Disposable Home Assistant rejected a bootstrap request.")
        return await response.json(content_type=None)


async def wait_for_home_assistant(base_url: str, token: str = "") -> None:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for _ in range(120):
            try:
                if token:
                    payload = await json_request(
                        session, "GET", f"{base_url}/api/config", token=token
                    )
                    if payload.get("state") == "RUNNING":
                        return
                else:
                    async with session.get(f"{base_url}/api/onboarding") as response:
                        if response.status == 200:
                            return
            except (aiohttp.ClientError, asyncio.TimeoutError, BakeFailure):
                pass
            await asyncio.sleep(1)
    raise BakeFailure("Disposable Home Assistant did not become ready.")


async def bootstrap_disposable_home_assistant(base_url: str) -> str:
    await wait_for_home_assistant(base_url)
    client_id = f"{base_url}/"
    username = f"rc2dev8_{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(32)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        onboarding = await json_request(session, "GET", f"{base_url}/api/onboarding")
        if any(row.get("step") == "user" and row.get("done") for row in onboarding):
            raise BakeFailure("Disposable Home Assistant was not a fresh instance.")
        user = await json_request(
            session,
            "POST",
            f"{base_url}/api/onboarding/users",
            body={
                "client_id": client_id,
                "name": "RC2dev8 Disposable Administrator",
                "username": username,
                "password": password,
                "language": "en",
            },
        )
        auth_code = user.get("auth_code")
        if not auth_code:
            raise BakeFailure("Disposable Home Assistant did not issue an authorization code.")
        token_payload = await json_request(
            session,
            "POST",
            f"{base_url}/auth/token",
            form={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": client_id,
            },
        )
        token = token_payload.get("access_token")
        if not token:
            raise BakeFailure("Disposable Home Assistant did not issue an access token.")
        steps = {row.get("step"): bool(row.get("done")) for row in onboarding}
        if not steps.get("core_config"):
            await json_request(
                session,
                "POST",
                f"{base_url}/api/onboarding/core_config",
                body={},
                token=token,
            )
        if not steps.get("integration"):
            await json_request(
                session,
                "POST",
                f"{base_url}/api/onboarding/integration",
                body={"client_id": client_id, "redirect_uri": client_id},
                token=token,
            )
        if not steps.get("analytics"):
            await json_request(
                session,
                "POST",
                f"{base_url}/api/onboarding/analytics",
                body={},
                token=token,
            )
    await wait_for_home_assistant(base_url, token)
    return str(token)


async def verify_disposable_fixture(base_url: str, token: str) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        config = await json_request(
            session, "GET", f"{base_url}/api/config", token=token
        )
        states = await json_request(
            session, "GET", f"{base_url}/api/states", token=token
        )
    require(config.get("version") == HOME_ASSISTANT_VERSION, "Disposable Home Assistant version mismatch.")
    require(
        any(row.get("entity_id") == TARGET_ENTITY for row in states),
        "Disposable target entity is missing.",
    )
    automations = [
        row
        for row in states
        if str(row.get("entity_id") or "").startswith("automation.")
    ]
    require(
        any((row.get("attributes") or {}).get("id") == "rc2dev8_exact_image_fixture" for row in automations),
        "Disposable automation fixture is missing.",
    )
    return {
        "home_assistant_version": config.get("version"),
        "automation_count": len(automations),
        "target_entity_present": True,
    }


def rpc_message(text: str, content_type: str) -> dict[str, Any] | None:
    candidates = [text]
    if "text/event-stream" in content_type.lower():
        candidates = [
            line[5:].strip()
            for line in text.splitlines()
            if line.startswith("data:") and line[5:].strip()
        ]
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def tool_envelope(message: dict[str, Any] | None) -> dict[str, Any] | None:
    result = (message or {}).get("result") or {}
    for item in result.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            value = json.loads(str(item.get("text") or ""))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


class RawMcpClient:
    def __init__(self, port: int, access_secret: str):
        self._endpoint = f"http://127.0.0.1:{port}/{access_secret}/mcp"
        self._session: aiohttp.ClientSession | None = None
        self._next_id = 1
        self._session_id = ""

    async def __aenter__(self) -> "RawMcpClient":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=90),
            trust_env=False,
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
        )
        return self

    async def __aexit__(self, *_args) -> None:
        if self._session:
            await self._session.close()

    async def post(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        if not self._session:
            raise BakeFailure("The disposable MCP client is not initialized.")
        headers = {"mcp-protocol-version": "2025-03-26"}
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        try:
            async with self._session.post(
                self._endpoint, json=payload, headers=headers
            ) as response:
                text = await response.text()
                session_id = response.headers.get("mcp-session-id")
                if session_id:
                    self._session_id = session_id
                return response.status, rpc_message(
                    text, response.headers.get("content-type", "")
                )
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise BakeFailure("The disposable MCP endpoint could not be reached.") from None

    async def request(self, method: str, params: dict[str, Any] | None = None):
        request_id = self._next_id
        self._next_id += 1
        status, message = await self.post(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )
        return status, message

    async def initialize(self) -> None:
        status, message = await self.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "rc2dev8-exact-image-bake", "version": "1"},
            },
        )
        if status != 200 or not message or "result" not in message:
            raise BakeFailure("Disposable MCP initialization failed.")
        initialized_status, _ = await self.post(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        if initialized_status not in {200, 202, 204}:
            raise BakeFailure("Disposable MCP initialization completion failed.")

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        status, message = await self.request(
            "tools/call", {"name": name, "arguments": arguments}
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        envelope = tool_envelope(message)
        if status != 200 or envelope is None:
            raise BakeFailure(f"Disposable {name} did not return an Engineering envelope.")
        return envelope, elapsed_ms


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BakeFailure(message)


def index_from_analysis(envelope: dict[str, Any]) -> dict[str, Any]:
    value = ((envelope.get("data") or {}).get("index") or {})
    if not isinstance(value, dict):
        raise BakeFailure("Dependency analysis omitted index evidence.")
    return value


def index_from_health(envelope: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    dependency = ((envelope.get("data") or {}).get("dependency_analysis") or {})
    index = dependency.get("index") or {}
    if not isinstance(index, dict):
        raise BakeFailure("Server health omitted dependency-index state.")
    return dependency, index


def image_identity() -> dict[str, Any]:
    def field(template: str) -> str:
        return docker(["image", "inspect", ENGINEERING_IMAGE, "--format", template])

    expected_config = os.environ.get("RC2DEV8_PLATFORM_CONFIG_DIGEST", "")
    platform_digest = os.environ.get("RC2DEV8_PLATFORM_DIGEST", "")
    require(expected_config.startswith("sha256:"), "Selected platform config digest was not supplied.")
    require(platform_digest.startswith("sha256:"), "Selected platform manifest digest was not supplied.")
    identity = {
        "image_id": field("{{.Id}}"),
        "platform_manifest_digest": platform_digest,
        "platform_config_digest": expected_config,
        "architecture": field("{{.Architecture}}"),
        "os": field("{{.Os}}"),
        "repo_digests": json.loads(field("{{json .RepoDigests}}") or "[]"),
        "revision": field('{{index .Config.Labels "org.opencontainers.image.revision"}}'),
        "version": field('{{index .Config.Labels "org.opencontainers.image.version"}}'),
        "created": field('{{index .Config.Labels "org.opencontainers.image.created"}}'),
        "dirty": field('{{index .Config.Labels "io.hass-mcp.build.dirty"}}'),
        "attestation_manifest_count": int(
            os.environ.get("RC2DEV8_ATTESTATION_COUNT", "0") or 0
        ),
    }
    require(identity["revision"] == ENGINEERING_REVISION, "Local image revision is not RC2dev8.")
    require(identity["version"] == ENGINEERING_VERSION, "Local image version is not RC2dev8.")
    require(identity["created"] == ENGINEERING_CREATED, "Local image creation time is unexpected.")
    require(identity["dirty"] == "false", "Local image dirty label is not false.")
    require(identity["image_id"] == expected_config, "Local image ID does not match the selected platform config digest.")
    require(identity["attestation_manifest_count"] >= 3, "Platform attestations were not verified.")
    return identity


async def wait_for_engineering(port: int) -> None:
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        for _ in range(120):
            try:
                async with session.get(f"http://127.0.0.1:{port}/health") as response:
                    if response.status == 200:
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(1)
    raise BakeFailure("Disposable Engineering server did not become ready.")


async def run_bake(work: Path, names: dict[str, str]) -> dict[str, Any]:
    ha_port = free_loopback_port()
    engineering_port = free_loopback_port()
    ha_url = f"http://127.0.0.1:{ha_port}"
    write_fixture_configuration(work / "ha")
    write_engineering_options(work / "engineering")

    docker(["network", "create", "--label", RESOURCE_LABEL, names["network"]])
    docker(
        [
            "run", "-d", "--name", names["ha"], "--label", RESOURCE_LABEL,
            "--network", names["network"], "--network-alias", names["ha"],
            "-p", f"127.0.0.1:{ha_port}:8123",
            "-v", f"{work / 'ha'}:/config",
            HOME_ASSISTANT_IMAGE,
        ],
        timeout=300,
    )
    token = await bootstrap_disposable_home_assistant(ha_url)
    fixture = await verify_disposable_fixture(ha_url, token)
    access_secret = secrets.token_urlsafe(32)
    environment = dict(os.environ)
    environment.update({"HA_TOKEN": token, "ACCESS_SECRET": access_secret})
    docker(
        [
            "run", "-d", "--name", names["engineering"], "--label", RESOURCE_LABEL,
            "--network", names["network"],
            "-p", f"127.0.0.1:{engineering_port}:8100",
            "-v", f"{work / 'engineering'}:/data",
            "-e", "HA_TOKEN", "-e", "ACCESS_SECRET",
            "-e", f"HA_URL=http://{names['ha']}:8123",
            ENGINEERING_IMAGE,
        ],
        env=environment,
        timeout=180,
    )
    await wait_for_engineering(engineering_port)

    evidence: dict[str, Any] = {
        "schema_version": 1,
        "scenario": "rc2dev8_exact_image_dependency_refresh_failure",
        "started_at": utc_now(),
        "environment": {
            "engineering_image": ENGINEERING_IMAGE,
            "home_assistant_image": HOME_ASSISTANT_IMAGE,
            "soft_ttl_seconds": SOFT_TTL_SECONDS,
            "hard_ttl_seconds": HARD_TTL_SECONDS,
            "prewarm_enabled": False,
            "network_scope": "disposable_private_docker_network",
            "credentials": "synthetic_not_persisted",
        },
        "image": image_identity(),
        "fixture": fixture,
        "timeline": {},
    }
    args = {
        "entity_id": TARGET_ENTITY,
        "detail_level": "summary",
        "include_indirect": False,
        "max_depth": 2,
        "source_types": [],
        "limit": 5,
        "cursor": "",
    }
    async with RawMcpClient(engineering_port, access_secret) as client:
        await client.initialize()
        server, _ = await client.call("server_info", {"check_ha": False})
        server_data = server.get("data") or {}
        server_identity = server_data.get("server") or {}
        require(server_identity.get("version") == ENGINEERING_VERSION, "Runtime version mismatch.")
        require(server_identity.get("build_sha") == ENGINEERING_REVISION, "Runtime revision mismatch.")
        require(server_identity.get("build_dirty") is False, "Runtime dirty state is not false.")
        evidence["runtime"] = {
            "version": server_identity.get("version"),
            "build_sha": server_identity.get("build_sha"),
            "build_time": server_identity.get("build_time"),
            "build_dirty": server_identity.get("build_dirty"),
        }

        first, first_ms = await client.call(
            "entity_dependency_analysis", {**args, "refresh_index": True}
        )
        require(first.get("success") is True, "Initial dependency build failed.")
        first_index = index_from_analysis(first)
        first_data = first.get("data") or {}
        first_findings = first_data.get("findings") or []
        first_overview = first_data.get("overview") or {}
        generation_one = first_index.get("generation")
        fingerprint_one = first_index.get("fingerprint")
        require(isinstance(generation_one, int) and generation_one >= 1, "Initial generation is invalid.")
        require(bool(fingerprint_one), "Initial fingerprint is missing.")
        require(
            int(first_overview.get("direct_reference_count") or 0) >= 1
            and bool(first_findings),
            "Initial generation did not index the synthetic dependency.",
        )
        baseline_health, _ = await client.call("get_server_health", {"check_ha": False})
        baseline_dependency, baseline_index = index_from_health(baseline_health)
        baseline_build_count = int(baseline_dependency.get("index_build_count") or 0)
        baseline_failure_count = int(baseline_dependency.get("index_build_failures") or 0)
        require(baseline_build_count == 1, "Initial exact-image build count was not one.")
        require(baseline_failure_count == 0, "Initial exact-image build reported a failure.")
        evidence["timeline"]["initial_generation"] = {
            "generation": generation_one,
            "fingerprint": fingerprint_one,
            "foreground_ms": round(first_ms, 3),
            "build_count": baseline_build_count,
            "build_failures": baseline_failure_count,
            "build_state": baseline_index.get("build_state"),
            "freshness": baseline_index.get("freshness"),
            "request_profile": baseline_index.get("last_build_profile"),
            "direct_reference_count": first_overview.get("direct_reference_count"),
            "returned_finding_count": len(first_findings),
        }

        await asyncio.sleep(SOFT_TTL_SECONDS + 0.75)
        docker(["stop", "-t", "1", names["ha"]], timeout=30)
        stale, stale_ms = await client.call(
            "entity_dependency_analysis", {**args, "refresh_index": False}
        )
        require(stale.get("success") is True, "Soft-expired evidence was not returned.")
        stale_index = index_from_analysis(stale)
        stale_data = stale.get("data") or {}
        stale_findings = stale_data.get("findings") or []
        require(stale_index.get("generation") == generation_one, "Soft refresh changed generation before success.")
        require(stale_index.get("fingerprint") == fingerprint_one, "Soft refresh changed the prior fingerprint.")
        require(stale_index.get("evidence_stale") is True, "Soft-expired evidence was not marked stale.")
        require(stale_index.get("freshness") == "stale_within_hard_ttl", "Soft-expired freshness was incorrect.")
        require(stale_index.get("serving_previous_generation") is True, "Previous generation was not identified.")
        require(
            [row.get("evidence_id") for row in stale_findings]
            == [row.get("evidence_id") for row in first_findings],
            "Soft-expired evidence did not preserve the prior findings.",
        )
        evidence["timeline"]["soft_expired_response"] = {
            "generation": stale_index.get("generation"),
            "fingerprint": stale_index.get("fingerprint"),
            "foreground_ms": round(stale_ms, 3),
            "freshness": stale_index.get("freshness"),
            "evidence_stale": stale_index.get("evidence_stale"),
            "serving_previous_generation": stale_index.get("serving_previous_generation"),
            "background_refresh_active_at_response": stale_index.get("background_refresh_active"),
            "evidence_age_seconds": stale_index.get("evidence_age_seconds"),
            "returned_finding_count": len(stale_findings),
        }

        failed_dependency: dict[str, Any] = {}
        failed_index: dict[str, Any] = {}
        for _ in range(80):
            health, _ = await client.call("get_server_health", {"check_ha": False})
            failed_dependency, failed_index = index_from_health(health)
            if (
                not failed_index.get("background_refresh_active")
                and int(failed_dependency.get("index_build_failures") or 0)
                > baseline_failure_count
            ):
                break
            await asyncio.sleep(0.25)
        require(failed_index.get("build_state") == "refresh_failed_stale_available", "Failed refresh state was not preserved.")
        require(failed_index.get("generation") == generation_one, "Failed refresh published a generation.")
        require(failed_index.get("fingerprint") == str(fingerprint_one)[:12], "Failed refresh lost the prior fingerprint.")
        require(bool(failed_index.get("last_refresh_failure_category")), "Failed refresh category was not recorded.")
        require(failed_index.get("background_refresh_active") is False, "Failed refresh remained active.")
        require(failed_index.get("serving_previous_generation") is True, "Failed refresh did not preserve the prior generation.")
        evidence["timeline"]["failed_background_refresh"] = {
            "generation": failed_index.get("generation"),
            "fingerprint": failed_index.get("fingerprint"),
            "build_state": failed_index.get("build_state"),
            "freshness": failed_index.get("freshness"),
            "build_count": failed_dependency.get("index_build_count"),
            "build_failures": failed_dependency.get("index_build_failures"),
            "last_refresh_failure_category": failed_index.get("last_refresh_failure_category"),
            "serving_previous_generation": failed_index.get("serving_previous_generation"),
        }

        current_age = float(failed_index.get("age_seconds") or 0.0)
        await asyncio.sleep(max(0.0, HARD_TTL_SECONDS + 0.75 - current_age))
        hard, hard_ms = await client.call(
            "entity_dependency_analysis", {**args, "refresh_index": False}
        )
        require(hard.get("success") is False, "Hard-expired evidence was silently returned.")
        require(not (hard.get("data") or {}).get("findings"), "Hard-expired findings were returned as current evidence.")
        hard_health, _ = await client.call("get_server_health", {"check_ha": False})
        hard_dependency, hard_index = index_from_health(hard_health)
        require(hard_index.get("generation") == generation_one, "Hard-expired failure published a generation.")
        require(hard_index.get("fingerprint") == str(fingerprint_one)[:12], "Hard-expired failure lost the prior fingerprint.")
        require(hard_index.get("freshness") == "hard_expired", "Hard-expired health was not explicit.")
        require(hard_index.get("valid") is False, "Hard-expired index remained valid.")
        evidence["timeline"]["hard_expired_refusal"] = {
            "success": hard.get("success"),
            "error_code": hard.get("error_code"),
            "foreground_ms": round(hard_ms, 3),
            "generation": hard_index.get("generation"),
            "fingerprint": hard_index.get("fingerprint"),
            "freshness": hard_index.get("freshness"),
            "valid": hard_index.get("valid"),
            "evidence_age_seconds": hard_index.get("evidence_age_seconds"),
            "maximum_evidence_age_seconds": hard_index.get("maximum_evidence_age_seconds"),
            "build_count": hard_dependency.get("index_build_count"),
            "build_failures": hard_dependency.get("index_build_failures"),
        }

        docker(["start", names["ha"]], timeout=60)
        await wait_for_home_assistant(ha_url, token)
        recovered, recovered_ms = await client.call(
            "entity_dependency_analysis", {**args, "refresh_index": True}
        )
        require(recovered.get("success") is True, "Recovery refresh failed.")
        recovered_index = index_from_analysis(recovered)
        require(recovered_index.get("generation") == generation_one + 1, "Recovery did not advance exactly one generation.")
        require(recovered_index.get("freshness") == "current", "Recovery evidence is not current.")
        require(recovered_index.get("evidence_stale") is False, "Recovery evidence remained stale.")
        require(recovered_index.get("serving_previous_generation") is False, "Recovery still reported the prior generation.")
        recovery_health, _ = await client.call("get_server_health", {"check_ha": False})
        recovery_dependency, recovery_index = index_from_health(recovery_health)
        recovery_build_count = int(recovery_dependency.get("index_build_count") or 0)
        require(recovery_index.get("background_refresh_active") is False, "Recovery left a background refresh active.")
        require(recovery_index.get("last_refresh_failure_category") is None, "Recovery did not clear the refresh failure category.")
        warm, warm_ms = await client.call(
            "entity_dependency_analysis", {**args, "refresh_index": False}
        )
        warm_index = index_from_analysis(warm)
        warm_health, _ = await client.call("get_server_health", {"check_ha": False})
        warm_dependency, _ = index_from_health(warm_health)
        require(warm.get("success") is True, "Warm post-recovery lookup failed.")
        require(warm_index.get("generation") == generation_one + 1, "Warm lookup changed generation.")
        require(warm_index.get("cache_hit") is True, "Warm lookup was not a cache hit.")
        require(int(warm_dependency.get("index_build_count") or 0) == recovery_build_count, "Warm lookup started another build.")
        require((warm.get("timing") or {}).get("home_assistant_request_count") == 0, "Warm lookup contacted Home Assistant.")
        evidence["timeline"]["recovery"] = {
            "generation": recovered_index.get("generation"),
            "fingerprint": recovered_index.get("fingerprint"),
            "foreground_ms": round(recovered_ms, 3),
            "freshness": recovered_index.get("freshness"),
            "evidence_stale": recovered_index.get("evidence_stale"),
            "serving_previous_generation": recovered_index.get("serving_previous_generation"),
            "build_state": recovery_index.get("build_state"),
            "build_count": recovery_build_count,
            "background_refresh_active": recovery_index.get("background_refresh_active"),
            "last_refresh_failure_category": recovery_index.get("last_refresh_failure_category"),
        }
        evidence["timeline"]["warm_lookup"] = {
            "generation": warm_index.get("generation"),
            "fingerprint": warm_index.get("fingerprint"),
            "foreground_ms": round(warm_ms, 3),
            "cache_hit": warm_index.get("cache_hit"),
            "home_assistant_request_count": (warm.get("timing") or {}).get("home_assistant_request_count"),
            "build_count": warm_dependency.get("index_build_count"),
        }

    evidence["completed_at"] = utc_now()
    evidence["result"] = "PASS"
    evidence["assertions"] = {
        "exact_published_image": True,
        "generation_one_preserved_inside_hard_ttl": True,
        "failed_refresh_recorded": True,
        "partial_generation_not_published": True,
        "hard_expired_evidence_refused": True,
        "recovery_generation_advanced_once": True,
        "warm_lookup_zero_inventory_rebuild": True,
        "production_target_possible": False,
    }
    assert_sanitized(evidence, (token, access_secret))
    return evidence


def assert_sanitized(value: Any, sensitive_values: tuple[str, ...]) -> None:
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=False)
    for secret in sensitive_values:
        if secret and secret in rendered:
            raise BakeFailure("The sanitized evidence contained a synthetic credential.")
    lowered = rendered.lower()
    for marker in ("authorization", "access_token", "refresh_token", "password"):
        if marker in lowered:
            raise BakeFailure("The sanitized evidence contained a credential field.")


def cleanup(names: dict[str, str]) -> None:
    for key in ("engineering", "ha"):
        try:
            docker(["rm", "-f", names[key]], timeout=30)
        except BakeFailure:
            pass
    try:
        docker(["network", "rm", names["network"]], timeout=30)
    except BakeFailure:
        pass


async def async_main(args: argparse.Namespace) -> int:
    if not args.acknowledge_disposable_exact_image:
        raise SystemExit("Refusing to create Docker resources without the disposable-test acknowledgement.")
    if os.environ.get("RC2DEV8_DISPOSABLE_EXACT_IMAGE") != "1":
        raise SystemExit("RC2DEV8_DISPOSABLE_EXACT_IMAGE=1 is required.")
    if not shutil.which("docker"):
        raise SystemExit("Docker is required for the exact-image bake.")

    suffix = secrets.token_hex(5)
    names = {
        "network": f"rc2dev8-bake-net-{suffix}",
        "ha": f"rc2dev8-bake-ha-{suffix}",
        "engineering": f"rc2dev8-bake-engineering-{suffix}",
    }
    failure = False
    try:
        with tempfile.TemporaryDirectory(prefix="rc2dev8-exact-image-") as directory:
            try:
                evidence = await run_bake(Path(directory), names)
            except Exception:
                failure = True
                evidence = {
                    "schema_version": 1,
                    "scenario": "rc2dev8_exact_image_dependency_refresh_failure",
                    "completed_at": utc_now(),
                    "result": "FAIL",
                    "failure_category": "bounded_acceptance_failure",
                    "credentials": "synthetic_not_persisted",
                }
            assert_sanitized(evidence, ())
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    finally:
        cleanup(names)
    if failure:
        raise SystemExit("RC2dev8 disposable exact-image bake failed; sanitized evidence was preserved.")
    print("RC2dev8 disposable exact-image bake: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
