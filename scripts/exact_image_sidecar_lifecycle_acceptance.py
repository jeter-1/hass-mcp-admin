"""Bounded lifecycle acceptance for the immutable ha-mcp 8.1 image.

This script is copied into and executed by the digest-pinned standalone image.
It exercises the packaged stdio settings sidecar without printing its secret
URL, then checks the packaged CLI's bounded pending-task cleanup paths.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


MAX_EVIDENCE_BYTES = 100_000
SIDECAR_START_TIMEOUT_SECONDS = 15.0
SIDECAR_STOP_TIMEOUT_SECONDS = 8.0


class LifecycleAcceptanceFailure(RuntimeError):
    """One exact-image lifecycle assertion failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LifecycleAcceptanceFailure(message)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _listener_addresses(port: int) -> list[str]:
    addresses: list[str] = []
    for path, family in (
        (Path("/proc/net/tcp"), socket.AF_INET),
        (Path("/proc/net/tcp6"), socket.AF_INET6),
    ):
        try:
            lines = path.read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "0A":
                continue
            raw_host, raw_port = fields[1].split(":", 1)
            if int(raw_port, 16) != port:
                continue
            packed = bytes.fromhex(raw_host)
            if family == socket.AF_INET:
                packed = packed[::-1]
            else:
                packed = b"".join(
                    packed[index : index + 4][::-1]
                    for index in range(0, 16, 4)
                )
            addresses.append(socket.inet_ntop(family, packed))
    return sorted(addresses)


def _wait_for_capture(
    data_dir: Path,
    process: subprocess.Popen[bytes],
) -> dict[str, Any]:
    url_path = data_dir / "ui.url"
    pid_path = data_dir / "ui.pid"
    state_path = data_dir / "ui.state"
    deadline = time.monotonic() + SIDECAR_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LifecycleAcceptanceFailure(
                "settings sidecar exited before publishing discovery state"
            )
        if url_path.is_file() and pid_path.is_file() and state_path.is_file():
            try:
                published_pid = pid_path.read_text(
                    encoding="ascii"
                ).strip()
            except OSError:
                published_pid = ""
            if published_pid == str(process.pid):
                break
        time.sleep(0.05)
    else:
        raise LifecycleAcceptanceFailure(
            "settings sidecar did not publish bounded discovery state"
        )

    url = url_path.read_text(encoding="utf-8").strip()
    parsed = urlsplit(url)
    require(parsed.scheme == "http", "sidecar URL scheme changed")
    require(parsed.hostname == "127.0.0.1", "sidecar URL is not loopback-only")
    require(isinstance(parsed.port, int), "sidecar URL omitted its port")
    require(parsed.path.endswith("/settings"), "sidecar settings route changed")
    require(
        pid_path.read_text(encoding="ascii").strip() == str(process.pid),
        "sidecar PID discovery file did not identify the serving process",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    require(
        isinstance(state, dict)
        and state.get("port") == parsed.port
        and isinstance(state.get("secret_path"), str)
        and parsed.path == f"{state['secret_path']}/settings",
        "sidecar persisted identity did not match the serving URL",
    )
    addresses = _listener_addresses(parsed.port)
    require(addresses == ["127.0.0.1"], "sidecar listener is not loopback-only")
    opener = build_opener(ProxyHandler({}))
    with opener.open(url, timeout=3.0) as response:
        require(response.status == 200, "sidecar settings route was not healthy")

    modes = {
        name: stat.S_IMODE(path.stat().st_mode)
        for name, path in (
            ("state", state_path),
            ("url", url_path),
            ("pid", pid_path),
        )
    }
    require(set(modes.values()) == {0o600}, "sidecar discovery files are not mode 0600")
    return {
        "url_sha256": sha256_text(url),
        "secret_path_sha256": sha256_text(state["secret_path"]),
        "port": parsed.port,
        "listener_addresses": addresses,
        "file_modes": {name: oct(mode) for name, mode in modes.items()},
        "http_status": 200,
    }


def _retire_sidecar(data_dir: Path, process: subprocess.Popen[bytes]) -> dict[str, Any]:
    url = (data_dir / "ui.url").read_text(encoding="utf-8").strip()
    shutdown_url = url.removesuffix("/settings") + (
        "/api/settings/shutdown?mode=retire"
    )
    opener = build_opener(ProxyHandler({}))
    request = Request(shutdown_url, data=b"", method="POST")
    with opener.open(request, timeout=3.0) as response:
        payload = json.loads(response.read(MAX_EVIDENCE_BYTES))
        status = response.status
    require(status == 200, "sidecar retire request failed")
    require(
        isinstance(payload, dict)
        and payload.get("success") is True
        and payload.get("sentinel_created") is False,
        "sidecar retire response changed",
    )
    process.wait(timeout=SIDECAR_STOP_TIMEOUT_SECONDS)
    require(process.returncode == 0, "sidecar did not stop cleanly")
    require((data_dir / "ui.state").is_file(), "sidecar removed persistent identity")
    require(not (data_dir / "ui.url").exists(), "sidecar URL survived shutdown")
    require(not (data_dir / "ui.pid").exists(), "sidecar PID survived shutdown")
    require(
        not (data_dir / "settings_ui_disabled").exists(),
        "retirement disabled future sidecars",
    )
    return {
        "http_status": status,
        "exit_code": process.returncode,
        "state_retained": True,
        "serving_files_removed": True,
        "disabled_sentinel_created": False,
    }


def _coordinated_spawn(data_dir: Path) -> subprocess.Popen[bytes]:
    """Spawn through the packaged parent coordinator and capture its child."""
    environment = {
        "HA_MCP_CONFIG_DIR": str(data_dir),
        "HA_MCP_DISABLE_SETTINGS_UI": "false",
        "HOMEASSISTANT_URL": "http://127.0.0.1:18123",
        "HOMEASSISTANT_TOKEN": "synthetic-sidecar-lifecycle-token",
        "NO_PROXY": "*",
        "no_proxy": "*",
    }
    spawned: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def capture_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    with patch.dict(os.environ, environment):
        import ha_mcp.stdio_settings_sidecar as sidecar
        from ha_mcp.utils.data_paths import get_data_dir

        # Other packaged imports may have memoized a default data directory.
        # The coordinator and child must both use this probe's disposable root.
        get_data_dir.cache_clear()
        try:
            with patch.object(
                sidecar.subprocess, "Popen", side_effect=capture_popen
            ):
                sidecar.maybe_spawn()
        finally:
            get_data_dir.cache_clear()
    require(
        len(spawned) == 1,
        "packaged sidecar coordinator did not spawn exactly one generation",
    )
    return spawned[0]


def inspect_sidecar_lifecycle() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ha-mcp-sidecar-acceptance-") as raw:
        data_dir = Path(raw)
        process: subprocess.Popen[bytes] | None = None
        runs: list[dict[str, Any]] = []
        try:
            process = _coordinated_spawn(data_dir)
            first = _wait_for_capture(data_dir, process)
            first_process = process
            process = _coordinated_spawn(data_dir)
            replacement_process = process
            restarted = _wait_for_capture(data_dir, replacement_process)
            require(
                restarted["url_sha256"] == first["url_sha256"]
                and restarted["secret_path_sha256"]
                == first["secret_path_sha256"],
                "sidecar identity changed across restart",
            )
            first_process.wait(timeout=SIDECAR_STOP_TIMEOUT_SECONDS)
            require(
                first_process.returncode == 0,
                "replaced sidecar did not stop cleanly",
            )
            require(
                replacement_process.poll() is None,
                "replacement sidecar was not the serving generation",
            )
            runs.append(
                {
                    "kind": "replaced_generation",
                    **first,
                    "replacement_exit_code": first_process.returncode,
                }
            )
            process = replacement_process
            restarted["retire"] = _retire_sidecar(data_dir, process)
            runs.append({"kind": "replacement", **restarted})
            process = None

            corrupt_sha256 = sha256_text("{not-valid-json")
            (data_dir / "ui.state").write_text(
                "{not-valid-json", encoding="utf-8"
            )
            process = _coordinated_spawn(data_dir)
            regenerated = _wait_for_capture(data_dir, process)
            require(
                regenerated["url_sha256"] != first["url_sha256"]
                and regenerated["secret_path_sha256"]
                != first["secret_path_sha256"],
                "corrupt sidecar state was not regenerated",
            )
            regenerated["corrupt_input_sha256"] = corrupt_sha256
            regenerated["retire"] = _retire_sidecar(data_dir, process)
            runs.append({"kind": "corrupt_state_regeneration", **regenerated})
            process = None
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)

    return {
        "stable_identity_across_restart": True,
        "live_generation_replaced": True,
        "single_serving_generation": True,
        "corrupt_state_regenerated_identity": True,
        "loopback_only_binding": True,
        "shutdown_cleanup": True,
        "runs": runs,
    }


async def inspect_pending_task_cancellation() -> dict[str, Any]:
    import ha_mcp.__main__ as main_module

    events: list[str] = []

    async def stream() -> AsyncIterator[None]:
        try:
            while True:
                await asyncio.sleep(3600)
                yield None
        finally:
            events.append("generator_finalized")

    async def reader() -> None:
        async for _ in stream():
            pass

    reader_task = asyncio.create_task(reader())
    watcher_task = asyncio.create_task(asyncio.sleep(3600))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await main_module._cancel_tasks(reader_task, watcher_task)
    require(reader_task.cancelled(), "pending reader was not cancelled")
    require(watcher_task.cancelled(), "pending watcher was not cancelled")
    require(events == ["generator_finalized"], "pending reader was not finalized")

    release = asyncio.Event()
    cancellation_seen = asyncio.Event()
    cleanup_task: asyncio.Task[Any] | None = None

    async def swallowing_cleanup() -> None:
        nonlocal cleanup_task
        cleanup_task = asyncio.current_task()
        while True:
            try:
                await release.wait()
                return
            except asyncio.CancelledError:
                cancellation_seen.set()

    async def run_forever(*, show_banner: bool) -> None:
        del show_banner
        await asyncio.sleep(3600)

    server = SimpleNamespace(run_async=run_forever)
    previous_timeout = main_module.SHUTDOWN_TIMEOUT_SECONDS
    main_module.SHUTDOWN_TIMEOUT_SECONDS = 0.05
    main_module._shutdown_event = None
    main_module._shutdown_in_progress = False
    started = time.monotonic()
    server_task: asyncio.Task[Any] | None = None
    try:
        with patch.object(main_module, "_get_mcp", return_value=server), patch.object(
            main_module, "_cleanup_resources", side_effect=swallowing_cleanup
        ):
            server_task = asyncio.create_task(
                main_module._run_with_graceful_shutdown()
            )
            await asyncio.sleep(0.02)
            require(
                main_module._shutdown_event is not None,
                "shutdown event was not initialized",
            )
            main_module._shutdown_event.set()
            done, pending = await asyncio.wait({server_task}, timeout=2.0)
            require(not pending and server_task in done, "bounded shutdown hung")
            try:
                server_task.result()
            except asyncio.CancelledError:
                pass
            await asyncio.wait_for(cancellation_seen.wait(), timeout=1.0)
    finally:
        release.set()
        if cleanup_task is not None:
            await asyncio.wait_for(asyncio.shield(cleanup_task), timeout=1.0)
        if server_task is not None and not server_task.done():
            server_task.cancel()
            await asyncio.wait({server_task}, timeout=1.0)
        main_module.SHUTDOWN_TIMEOUT_SECONDS = previous_timeout

    elapsed = time.monotonic() - started
    require(elapsed < 2.0, "shutdown cleanup exceeded its bounded deadline")
    return {
        "reader_cancelled": True,
        "watcher_cancelled": True,
        "generator_finalized": True,
        "cleanup_cancellation_observed": True,
        "shutdown_completed_within_2s": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    failure = False
    try:
        from ha_mcp._version import get_version

        require(
            get_version() == args.expected_version,
            "packaged runtime version changed",
        )
        result = {
            "result": "PASS",
            "model": "ha-mcp-exact-image-lifecycle-evidence-v1",
            "upstream_version": args.expected_version,
            "sidecar": inspect_sidecar_lifecycle(),
            "shutdown": asyncio.run(inspect_pending_task_cancellation()),
        }
    except Exception as exc:
        failure = True
        result = {
            "result": "FAIL",
            "model": "ha-mcp-exact-image-lifecycle-evidence-v1",
            "upstream_version": args.expected_version,
            "failure_type": type(exc).__name__[:96],
        }
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    require(len(encoded.encode("utf-8")) <= MAX_EVIDENCE_BYTES, "evidence too large")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    if failure:
        raise SystemExit(
            "exact-image lifecycle acceptance failed; see bounded evidence"
        )


if __name__ == "__main__":
    main()
