"""HAMCP-319 section 5.1 case ``91_typed_controls_verified_readback`` (91_only).

Proves, against the exact digest-pinned ha-mcp 9.1.2 image (HAMCP-351; 9.1.0
before it) named by the lane's
reviewed registry row and an exact digest-pinned disposable Home Assistant Core
from the reviewed support window, that Engineering's typed power, fan and helper
controls each go plan/dispatch -> apply -> verified independent readback through
the ha-mcp 9.1.2 provider on the strict full-catalog endpoint
``/synthetic-upstream-secret/mcp/catalog/full``.

Everything is disposable and loopback-only: the Core container is published on
127.0.0.1 only, the peer listens on 127.0.0.1 only, every credential is a
synthetic onboarding token minted for this run, and the Core typed authority is
an ephemeral Ed25519 test registry. No caller-supplied image, endpoint or
credential is accepted.

The script writes exactly one file, ``<cases-dir>/91_typed_controls_verified_readback.json``,
and exits nonzero unless every control executed and verified. It never writes
``skipped``.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from typing import Any, Awaitable, Callable
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT / "scripts", ROOT / "hass_mcp_engineering_beta"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from exact_image_admission_lanes import (  # noqa: E402
    DIGEST, EXPECTED_PROTOCOL, LaneFailure, bounded, case_result, classify_form, require, write_json,
)

CASE = "91_typed_controls_verified_readback"
FORM = "strict_full"
JOB = "exact-image-admission-lanes"
UPSTREAM_VERSION = "9.1.2"
SECRET_PATH = "/synthetic-upstream-secret/mcp"
STRICT_PATH = SECRET_PATH + "/catalog/full"
# 9.1 answers 404 on this short form; it is never a usable endpoint here.
SHORT_PATH = "/synthetic-upstream-secret/catalog/full"
PEER_REPOSITORY = "ghcr.io/homeassistant-ai/ha-mcp"
PEER_IMAGE = re.compile(r"ghcr\.io/homeassistant-ai/ha-mcp@sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
CORE_REPOSITORY = "ghcr.io/home-assistant/home-assistant"
PORT_RANGE = range(18400, 18500)
DEFAULT_CORE_PORT = 18423
DEFAULT_PEER_PORT = 18486
PREFIX = "typed91"
# The Core image alone needs about 3.5 GB unpacked (more on a containerd store);
# refuse to start rather than exhaust a shared host's disk.
MIN_DOCKER_FREE_BYTES = 6 * 1024 ** 3
LABEL = "io.hass-mcp.typed91"
CONTROLS = ("power", "fan", "helper")
FAN = "fan.hamcp_contract_fan"
POWER_TARGETS = ("light.hamcp_contract_light", "switch.hamcp_contract_switch")
# (domain, action, value, expected independent state)
HELPER_OPERATIONS = (
    ("input_boolean", "turn_on", None, "on"),
    ("input_boolean", "turn_off", None, "off"),
    ("input_number", "set_value", 21.5, "21.5"),
    ("input_select", "select_option", "Away", "Away"),
)
CORE_CONFIGURATION = """\
homeassistant:
  name: HAMCP-319 typed 9.1 disposable fixture
  time_zone: UTC
default_config:
automation: []
script: {}
input_boolean:
  hamcp_seq17:
    name: HAMCP seq17 boolean
    initial: false
input_number:
  hamcp_seq17:
    name: HAMCP seq17 number
    min: 10
    max: 30
    step: 0.5
    initial: 20
    mode: box
input_select:
  hamcp_seq17:
    name: HAMCP seq17 select
    options: [Home, Away, Night]
    initial: Home
"""
FIXTURES = (
    ROOT / "tests/fixtures/real_ha_power/custom_components/power_contract_fixture",
    ROOT / "tests/fixtures/real_ha_device_migration/custom_components/beta23_device_fixture",
)


# --------------------------------------------------------------------------
# Pure, offline-testable selection and evaluation
# --------------------------------------------------------------------------

def reviewed_release(version: str):
    """The compiled reviewed registry release for ``version`` (no network)."""

    from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry
    return load_reviewed_upstream_release_registry().by_version.get(version)


def select_peer_image(peers: Any, architecture: str,
                      release_for: Callable[[str], Any] = reviewed_release) -> dict[str, str]:
    """Derive the peer image only from the lane's ``candidate`` registry row.

    The row must name ha-mcp 9.1.2 by immutable index digest and agree exactly
    with the compiled reviewed registry entry (index, per-platform manifests,
    image revision and source commit). Nothing else can supply an image.
    """

    require(architecture in ("amd64", "arm64"), "peer_architecture_invalid")
    require(isinstance(peers, dict) and isinstance(peers.get("candidate"), dict), "peer_row_absent")
    row = peers["candidate"]
    image, index = row.get("upstream_image"), row.get("image_index_digest")
    platforms = row.get("architecture_image_digests")
    require(row.get("upstream_version") == UPSTREAM_VERSION, "peer_version_not_9_1_2")
    require(isinstance(image, str) and PEER_IMAGE.fullmatch(image) is not None, "peer_image_not_digest_pinned")
    require(isinstance(index, str) and DIGEST.fullmatch(index) is not None
            and image == PEER_REPOSITORY + "@" + index, "peer_image_index_mismatch")
    require(isinstance(platforms, dict) and set(platforms) == {"linux/amd64", "linux/arm64"}
            and all(isinstance(v, str) and DIGEST.fullmatch(v) for v in platforms.values()),
            "peer_platform_digests_malformed")
    revision, source = row.get("image_revision"), row.get("source_commit")
    require(isinstance(revision, str) and COMMIT.fullmatch(revision) is not None
            and isinstance(source, str) and COMMIT.fullmatch(source) is not None, "peer_revision_malformed")
    release = release_for(UPSTREAM_VERSION)
    require(release is not None and not getattr(release, "revoked", False), "peer_release_unreviewed")
    require(release.image_index_digest == index
            and dict(release.architecture_image_digests_by_platform) == platforms
            and release.image_revision == revision and release.source_commit == source,
            "peer_row_differs_from_reviewed_registry")
    return {"image": image, "image_index_digest": index, "platform": "linux/" + architecture,
            "platform_manifest_digest": platforms["linux/" + architecture],
            "image_revision": revision, "source_commit": source, "version": UPSTREAM_VERSION}


def core_image(window_loader: Callable[[], dict] | None = None) -> dict[str, str]:
    """The current Core slot of the reviewed support window, digest-pinned.

    ``support_window.load`` already refuses a slot without a reviewed lane
    binding or committed lane provenance; typed operations must be bound too.
    """

    import core_lane_bindings
    if window_loader is None:
        import support_window
        window_loader = support_window.load
    window = window_loader()
    current = window["core"]["current"]
    version, digest = current["version"], current["image_index_digest"]
    require(isinstance(digest, str) and DIGEST.fullmatch(digest) is not None, "core_image_not_digest_pinned")
    binding = core_lane_bindings.reviewed_lane_binding(version)
    require(binding is not None and binding.image_index_digest == digest, "core_binding_unreviewed")
    require(binding.typed_operations is True, "core_typed_operations_unbound")
    admission = window.get("admission") or {}
    require(admission.get("candidate_version") == UPSTREAM_VERSION, "admission_candidate_not_9_1_2")
    return {"image": f"{CORE_REPOSITORY}:{version}@{digest}", "version": version, "image_index_digest": digest}


def strict_endpoint(port: int) -> str:
    return validate_endpoint(f"http://127.0.0.1:{port}{STRICT_PATH}")


def validate_endpoint(url: str) -> str:
    """Accept only the loopback ``/synthetic-upstream-secret/mcp/catalog/full`` form."""

    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{5})(/[A-Za-z0-9/_-]+)", url or "")
    require(match is not None, "endpoint_not_loopback_strict_form")
    require(match.group(2) != SHORT_PATH, "endpoint_short_form_refused")
    require(match.group(2) == STRICT_PATH, "endpoint_not_strict_secret_path")
    require(int(match.group(1)) in PORT_RANGE, "endpoint_port_out_of_range")
    require(classify_form(url) == FORM, "endpoint_not_strict_full")
    return url


def check_ports(core_port: int, peer_port: int) -> None:
    require(core_port in PORT_RANGE and peer_port in PORT_RANGE and core_port != peer_port,
            "ports_outside_18400_18499_or_equal")


def job_gate(environ: dict[str, str]) -> None:
    if environ.get("GITHUB_ACTIONS") == "true":
        require(environ.get("GITHUB_JOB") == JOB, "not_exact_image_admission_lanes_job")


def negotiated(protocols: list[Any]) -> str | None:
    """The protocol actually observed, only when every exchange agreed."""

    if not protocols or not all(isinstance(p, str) for p in protocols) or len(set(protocols)) != 1:
        return None
    return protocols[0]


def evaluate(controls: dict[str, Any], protocols: list[Any]) -> tuple[str, str]:
    """``passed`` only when every control executed and verified on 2025-03-26."""

    for name in CONTROLS:
        control = controls.get(name)
        if not isinstance(control, dict) or control.get("executed") is not True:
            return "failed", f"typed {name} control was not executed"
        if control.get("verified") is not True:
            return "failed", f"typed {name} control was not verified by independent readback"
    if not protocols:
        return "failed", "no negotiated protocol was observed from the 9.1.2 peer"
    if negotiated(protocols) != EXPECTED_PROTOCOL:
        return "failed", "negotiated protocol was not exactly 2025-03-26 on every exchange"
    return "passed", ""


# --------------------------------------------------------------------------
# Lane plan and entry point
# --------------------------------------------------------------------------

@dataclass
class Plan:
    architecture: str
    peer: dict[str, str]
    core: dict[str, str]
    endpoint: str
    core_port: int
    peer_port: int
    work_root: Path
    identity: str
    # Filled progressively by the runner so a failure still reports evidence.
    evidence: dict[str, Any] = field(default_factory=lambda: {"controls": {}, "protocols": []})


def build_observed(plan: Plan | None, evidence: dict[str, Any]) -> dict[str, Any]:
    protocols = evidence.get("protocols") or []
    observed: dict[str, Any] = {"negotiated_protocol": negotiated(protocols)}
    observed["exchange_count"] = len(protocols)
    if plan is not None:
        observed.update(endpoint_path=STRICT_PATH, architecture=plan.architecture,
                        peer_image_index_digest=plan.peer["image_index_digest"],
                        peer_platform_manifest_digest=plan.peer["platform_manifest_digest"],
                        core_version=plan.core["version"],
                        core_image_index_digest=plan.core["image_index_digest"])
    for key in ("upstream_server", "read_admission", "core_compatible_count", "peer_image_id"):
        if key in evidence:
            observed[key] = evidence[key]
    observed["controls"] = {name: evidence["controls"].get(name, {"executed": False, "verified": False})
                            for name in CONTROLS}
    return observed


Runner = Callable[[Plan], Awaitable[None]]


def main(argv: list[str] | None = None, runner: Runner | None = None,
         environ: dict[str, str] | None = None,
         release_for: Callable[[str], Any] = reviewed_release,
         window_loader: Callable[[], dict] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--peers", required=True)
    parser.add_argument("--architecture", required=True, choices=("amd64", "arm64"))
    parser.add_argument("--cases-dir", required=True, type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--core-port", type=int, default=DEFAULT_CORE_PORT)
    parser.add_argument("--peer-port", type=int, default=DEFAULT_PEER_PORT)
    args = parser.parse_args(argv)
    environ = dict(os.environ) if environ is None else environ
    runner = run_lane if runner is None else runner
    plan: Plan | None = None
    evidence: dict[str, Any] = {"controls": {}, "protocols": []}
    status, reason = "failed", "lane did not complete"
    try:
        job_gate(environ)
        check_ports(args.core_port, args.peer_port)
        peers = json.loads(args.peers)
        peer = select_peer_image(peers, args.architecture, release_for)
        core = core_image(window_loader)
        endpoint = strict_endpoint(args.peer_port)
        work_root = args.work_dir or Path(environ.get("RUNNER_TEMP") or tempfile.gettempdir())
        identity = f"{PREFIX}-{os.getpid()}-{args.architecture}"
        plan = Plan(args.architecture, peer, core, endpoint, args.core_port, args.peer_port,
                    Path(work_root), identity, evidence)
        previous = signal.getsignal(signal.SIGTERM)

        def terminated(signum, frame):
            # A cancelled job still runs the runner's cleanup and writes ``failed``.
            raise LaneFailure("lane_terminated")

        signal.signal(signal.SIGTERM, terminated)
        try:
            asyncio.run(runner(plan))
        finally:
            signal.signal(signal.SIGTERM, previous)
        status, reason = evaluate(evidence["controls"], evidence["protocols"])
    except LaneFailure as exc:
        status, reason = "failed", "lane refused: " + bounded(str(exc), 120)
    except json.JSONDecodeError:
        status, reason = "failed", "lane refused: peers_not_json"
    except Exception as exc:  # bounded category only; never exception text
        status, reason = "failed", "lane error: " + type(exc).__name__[:64]
    finally:
        try:
            observed = build_observed(plan, evidence)
        except Exception:
            observed = {"negotiated_protocol": None}
            status, reason = "failed", "lane error: evidence_unbuildable"
        write_json(args.cases_dir / (CASE + ".json"),
                   case_result(CASE, status, form=FORM, reason=reason if status != "passed" else "",
                               observed=observed))
    print(json.dumps({"case": CASE, "status": status, "reason": reason}), flush=True)
    return 0 if status == "passed" else 1


# --------------------------------------------------------------------------
# Real lane (Docker, disposable Core, exact peer, in-process Engineering)
# --------------------------------------------------------------------------

def docker(*args: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], text=True, capture_output=True, timeout=timeout)
    if check:
        require(result.returncode == 0, "docker_" + (args[0] if args else "command") + "_failed")
    return result


def state_path(entity_id: str) -> str:
    from ha_mcp_engineering.clients.paths import rest_path
    return rest_path("/states/{}", entity_id, kinds=("entity_id",))


def operation_id() -> str:
    return f"{int(datetime.now(timezone.utc).timestamp())}-{uuid4().hex}"


class Observer:
    """Counts each provider's real exchanges and records the peer's protocol."""

    def __init__(self, evidence: dict[str, Any]):
        self.evidence = evidence
        self.exchanges: list[dict[str, Any]] = []

    def wrap(self, provider, name: str) -> None:
        transport = provider.transport
        require(transport is not None, name + "_provider_transport_unconfigured")
        original = transport.execute_read
        observer = self

        async def observed(tool, args, **kwargs):
            sent = {"value": False}
            before = kwargs.get("before_dispatch")
            if before is not None:
                async def counted():
                    await before()
                    sent["value"] = True
                kwargs["before_dispatch"] = counted
            try:
                value = await original(tool, args, **kwargs)
            except BaseException:
                observer.exchanges.append({"provider": name, "tool": tool, "sent": sent["value"],
                                           "complete": False})
                raise
            observer.exchanges.append({"provider": name, "tool": tool, "sent": sent["value"],
                                       "complete": True})
            observer.evidence["protocols"].append(value.protocol_version)
            observer.evidence["upstream_server"] = {"name": value.server_name, "version": value.server_version}
            return value

        transport.execute_read = observed

    def writes(self, name: str) -> int:
        return sum(1 for item in self.exchanges
                   if item["provider"] == name and item["tool"] == "ha_call_service" and item["sent"])


async def settle(service, request, *, status_key: str = "state"):
    receipt = await service.control(request)
    for _ in range(10):
        if receipt.get("terminal") is True:
            break
        await asyncio.sleep(2)
        receipt = await service.reconcile(request.task_id)
    return receipt


def core_binding_version(receipt: dict[str, Any]) -> Any:
    return (receipt.get("core_binding") or {}).get("version")


async def fan_control(plan: Plan, service, rest, telemetry, observer: Observer) -> None:
    from ha_mcp_engineering.fan.contracts import FAN_SEMANTIC_CONTRACTS, FanRequest, digest
    record = plan.evidence["controls"]["fan"] = {"executed": False, "verified": False, "operations": []}
    for action, percentage in (("turn_on", 50), ("set_percentage", 75), ("turn_off", None)):
        request = FanRequest(entity_id=FAN, action=action, percentage=percentage, operation_id=operation_id())
        telemetry.ordinary_fan_binding = digest(request.model_dump())
        start = observer.writes("fan")
        receipt = await settle(service, request)
        record["executed"] = True
        first = observer.writes("fan") - start
        duplicate = await service.control(request)
        repeated = observer.writes("fan") - start - first
        state = await rest.request("GET", state_path(FAN))
        readback = {"state": state.get("state"), "percentage": (state.get("attributes") or {}).get("percentage")}
        ok = (receipt.get("state") == "succeeded_verified" and receipt.get("provider_attempt_count") == 1
              and receipt.get("dispatch_intent_recorded") is True
              and receipt.get("provider") == "upstream_typed_fan"
              and receipt.get("provider_contract") == FAN_SEMANTIC_CONTRACTS[UPSTREAM_VERSION]
              and core_binding_version(receipt) == plan.core["version"]
              and duplicate == receipt and first == 1 and repeated == 0
              and readback["state"] == ("off" if action == "turn_off" else "on")
              and (percentage is None or readback["percentage"] == percentage))
        record["operations"].append(operation_evidence(FAN, action, receipt, first, repeated, readback, ok))
        require(ok, "fan_" + action + "_not_verified")
    health = service.health()
    require(not service.locks.records() and health["nonterminal_tasks"] == 0 and health["fallback_count"] == 0,
            "fan_not_settled")
    record["verified"] = True


async def power_control(plan: Plan, service, rest, telemetry, observer: Observer) -> None:
    from ha_mcp_engineering.power.contracts import POWER_SEMANTIC_CONTRACTS, PowerRequest, digest
    record = plan.evidence["controls"]["power"] = {"executed": False, "verified": False, "operations": []}
    for entity in POWER_TARGETS:
        initial = await rest.request("GET", state_path(entity))
        require(initial.get("state") == "off", "power_fixture_not_off")
        calls = initial["attributes"]["synthetic_service_calls"]
        for action in ("turn_on", "turn_off"):
            request = PowerRequest(entity_id=entity, action=action, operation_id=operation_id())
            telemetry.ordinary_power_binding = digest(request.model_dump())
            start = observer.writes("power")
            receipt = await settle(service, request)
            record["executed"] = True
            first = observer.writes("power") - start
            duplicate = await service.control(request)
            repeated = observer.writes("power") - start - first
            state = await rest.request("GET", state_path(entity))
            calls += 1
            readback = {"state": state.get("state"),
                        "synthetic_service_calls": (state.get("attributes") or {}).get("synthetic_service_calls")}
            ok = (receipt.get("state") == "succeeded_verified" and receipt.get("provider_attempt_count") == 1
                  and receipt.get("dispatch_intent_recorded") is True
                  and receipt.get("provider") == "upstream_typed_power"
                  and receipt.get("provider_contract") == POWER_SEMANTIC_CONTRACTS[UPSTREAM_VERSION]
                  and core_binding_version(receipt) == plan.core["version"]
                  and duplicate == receipt and first == 1 and repeated == 0
                  and readback["state"] == ("on" if action == "turn_on" else "off")
                  and readback["synthetic_service_calls"] == calls)
            record["operations"].append(operation_evidence(entity, action, receipt, first, repeated, readback, ok))
            require(ok, "power_" + action + "_not_verified")
    health = service.health()
    require(not service.locks.records() and health["nonterminal_tasks"] == 0 and health["fallback_count"] == 0
            and health["audit_projection_failures"] == 0, "power_not_settled")
    record["verified"] = True


def durable(receipt: dict[str, Any]) -> dict[str, Any]:
    # control() adds a current-call safety disclosure; persisted facts must match.
    return {key: value for key, value in receipt.items() if key != "safety"}


async def helper_control(plan: Plan, service, rest, observer: Observer) -> None:
    from ha_mcp_engineering.helper_value.contracts import HELPER_SEMANTIC_CONTRACTS, HelperRequest, digest
    from ha_mcp_engineering.request_context import begin_request, end_request
    record = plan.evidence["controls"]["helper"] = {"executed": False, "verified": False, "operations": []}

    async def control(request):
        telemetry, token = begin_request("typed91-helper")
        telemetry.ordinary_helper_value_binding = digest(request.model_dump())
        try:
            return await service.control(request)
        finally:
            end_request(token)

    for domain, action, value, expected in HELPER_OPERATIONS:
        entity = domain + ".hamcp_seq17"
        request = HelperRequest(entity_id=entity, action=action, value=value, operation_id=operation_id())
        start = observer.writes("helper")
        receipt = await control(request)
        confirmation = None
        if receipt.get("status") == "confirmation_required":
            # No dependency analysis is configured in this lane, so the service
            # must ask without dispatch; only the confirmed request may proceed.
            confirmation = receipt.get("reason")
            require(receipt.get("dispatch") == "no_dispatch" and receipt.get("task_created") is False
                    and observer.writes("helper") == start, "helper_confirmation_dispatched")
            request = request.model_copy(update={"acknowledge_safety_critical": True})
            receipt = await control(request)
        telemetry, token = begin_request("typed91-helper")
        try:
            for _ in range(10):
                if receipt.get("terminal") is True:
                    break
                await asyncio.sleep(2)
                receipt = await service.reconcile(request.task_id)
        finally:
            end_request(token)
        record["executed"] = True
        first = observer.writes("helper") - start
        duplicate = await control(request)
        repeated = observer.writes("helper") - start - first
        state = await rest.request("GET", state_path(entity))
        readback = {"state": state.get("state")}
        ok = (receipt.get("status") == "succeeded_verified" and receipt.get("provider_attempt_count") == 1
              and receipt.get("dispatch_intent_recorded") is True
              and receipt.get("provider") == "upstream_typed_helper_value"
              and receipt.get("provider_contract") == HELPER_SEMANTIC_CONTRACTS[UPSTREAM_VERSION]
              and core_binding_version(receipt) == plan.core["version"]
              and durable(duplicate) == durable(receipt) and first == 1 and repeated == 0
              and readback["state"] == expected)
        item = operation_evidence(entity, action, receipt, first, repeated, readback, ok)
        item["confirmation_reason"] = confirmation
        record["operations"].append(item)
        require(ok, "helper_" + action + "_not_verified")
    health = service.health()
    require(not service.locks.records() and health["nonterminal_tasks"] == 0 and health["fallback_count"] == 0,
            "helper_not_settled")
    record["verified"] = True


def operation_evidence(entity, action, receipt, first, repeated, readback, ok) -> dict[str, Any]:
    return {"entity_id": entity, "action": action,
            "state": receipt.get("state"), "status": receipt.get("status"),
            "provider": receipt.get("provider"), "provider_contract": receipt.get("provider_contract"),
            "provider_attempt_count": receipt.get("provider_attempt_count"),
            "core_binding_version": core_binding_version(receipt),
            "upstream_dispatches": first, "duplicate_dispatches": repeated,
            "independent_readback": readback, "verified": ok}


async def guarded(name: str, plan: Plan, step: Awaitable[None]) -> None:
    """Run one control; a failure is recorded and never hides the others."""

    try:
        await step
    except LaneFailure as exc:
        plan.evidence["controls"].setdefault(name, {"executed": False, "verified": False})
        plan.evidence["controls"][name]["failure"] = bounded(str(exc), 80)
    except Exception as exc:
        plan.evidence["controls"].setdefault(name, {"executed": False, "verified": False})
        plan.evidence["controls"][name]["failure"] = type(exc).__name__[:64]


def write_core_config(config: Path) -> None:
    (config / "custom_components").mkdir(parents=True)
    for source in FIXTURES:
        require(source.is_dir(), "core_fixture_missing")
        shutil.copytree(source, config / "custom_components" / source.name)
    (config / "configuration.yaml").write_text(CORE_CONFIGURATION, encoding="utf-8")


async def wait_http(url: str, attempts: int = 90) -> int:
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as client:
        for _ in range(attempts):
            try:
                async with client.get(url, allow_redirects=False) as response:
                    return response.status
            except (aiohttp.ClientError, TimeoutError, OSError):
                pass
            await asyncio.sleep(1)
    raise LaneFailure("peer_start_timeout")


async def wait_states(rest, wanted: dict[str, str], attempts: int = 60) -> None:
    for _ in range(attempts):
        ready = True
        for entity, state in wanted.items():
            try:
                value = await rest.request("GET", state_path(entity))
                ready = ready and value.get("state") == state
            except Exception:
                ready = False
        if ready:
            return
        await asyncio.sleep(1)
    raise LaneFailure("core_fixture_entities_unavailable")


def docker_disk_preflight(free_bytes: Callable[[str], int] | None = None) -> int:
    """Refuse the lane when Docker's root filesystem lacks room for the images."""

    root = docker("info", "--format", "{{.DockerRootDir}}").stdout.strip()
    require(root.startswith("/"), "docker_root_unknown")
    free = (free_bytes or (lambda path: shutil.disk_usage(path).free))(root)
    require(free >= MIN_DOCKER_FREE_BYTES, "docker_disk_insufficient")
    return free


def cleanup(plan: Plan, config: Path | None) -> None:
    """Remove every container and network this run labeled, then its scratch."""

    listed = docker("ps", "-aq", "--filter", f"label={LABEL}={plan.identity}", check=False).stdout.split()
    if listed:
        docker("rm", "-f", *listed, check=False, timeout=120)
    docker("network", "rm", plan.identity, check=False)
    if config is not None and config.exists():
        # Core writes root-owned files; delete them with the same pinned image
        # (no network, no sudo) so the runner can remove its scratch.
        docker("run", "--rm", "--network", "none", "--label", f"{LABEL}={plan.identity}",
               "-v", f"{config}:/config", "--entrypoint", "find", plan.core["image"],
               "/config", "-mindepth", "1", "-delete", check=False, timeout=180)


async def run_lane(plan: Plan) -> None:
    logging.disable(logging.CRITICAL)
    import aiohttp
    import real_ha_contract_tests as existing
    from dataclasses import replace
    from mcp.server.fastmcp import FastMCP
    from core_registry_contract_lane import configure_with_test_authority
    from ha_mcp_engineering.audit import AuditLogger
    from ha_mcp_engineering.clients import HomeAssistantRestClient
    from ha_mcp_engineering.ha_core_readmission import CoreRuntime
    from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
    from ha_mcp_engineering.providers.upstream_fan import FanProvider
    from ha_mcp_engineering.providers.upstream_power import PowerProvider
    from ha_mcp_engineering.providers.upstream_helper_value import HelperProvider
    from ha_mcp_engineering.fan.service import FanService
    from ha_mcp_engineering.fan.authority import FanCoreAuthority
    from ha_mcp_engineering.power.service import PowerService, PowerCoreAuthority
    from ha_mcp_engineering.helper_value.service import HelperService, HelperCoreAuthority
    from ha_mcp_engineering.request_context import begin_request, end_request

    plan.work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=PREFIX + "-", dir=plan.work_root))
    os.chmod(work, 0o700)
    config = work / "core"
    arch = plan.architecture
    common = ["--label", f"{LABEL}={plan.identity}", "--security-opt", "no-new-privileges:true",
              "--pids-limit", "512"]
    core = gateway = fan = power = helper = None
    context = None
    try:
        docker_disk_preflight()
        # Disposable Core: exact reviewed digest, own bridge, published on loopback only.
        docker("pull", "--platform", "linux/" + arch, plan.core["image"], timeout=900)
        require(docker("image", "inspect", "--format", "{{.Architecture}}", plan.core["image"]).stdout.strip()
                == arch, "core_architecture_mismatch")
        write_core_config(config)
        docker("network", "create", "--label", f"{LABEL}={plan.identity}", plan.identity)
        docker("run", "-d", "--name", plan.identity + "-core", "--network", plan.identity, *common,
               "--memory", "2g", "-p", f"127.0.0.1:{plan.core_port}:8123", "-v", f"{config}:/config",
               plan.core["image"], timeout=120)
        core_url = f"http://127.0.0.1:{plan.core_port}"
        existing.HA_URL, existing.CLIENT_ID = core_url, core_url + "/"
        token = await existing.bootstrap_disposable_admin()
        configured = replace(existing.settings(token), upstream_dashboard_mcp_url=plan.endpoint,
                             audit_path=str(work / "audit.jsonl"))
        rest = HomeAssistantRestClient(configured)
        core = CoreRuntime()
        observed_core = await existing.wait_for_runtime_ready(rest)
        require(observed_core.get("version") == plan.core["version"], "core_version_mismatch")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
            await existing._advance_config_flow(client, token, "beta23_device_fixture", [{"slot": "a"}])
            await existing._advance_config_flow(client, token, "power_contract_fixture", [{}])
        await wait_states(rest, {FAN: "off", POWER_TARGETS[0]: "off", POWER_TARGETS[1]: "off",
                                 "input_boolean.hamcp_seq17": "off", "input_number.hamcp_seq17": "20.0",
                                 "input_select.hamcp_seq17": "Home"})
        await configure_with_test_authority(core, configured, cache_path=work / "core-cache.json",
                                            expected_image=plan.core["image"], core_version=plan.core["version"])
        plan.evidence["core_compatible_count"] = core.health_snapshot()["compatible_count"]
        require(plan.evidence["core_compatible_count"] == 20, "core_typed_authority_incomplete")

        # Exact ha-mcp 9.1.2 peer, image only from the reviewed registry row.
        image = plan.peer["image"]
        docker("pull", "--platform", plan.peer["platform"], image, timeout=600)
        require(docker("image", "inspect", "--format", "{{.Architecture}}", image).stdout.strip() == arch,
                "peer_architecture_mismatch")
        labels = json.loads(docker("image", "inspect", "--format", "{{json .Config.Labels}}", image).stdout
                            or "null") or {}
        require(labels.get("org.opencontainers.image.version") == UPSTREAM_VERSION, "peer_version_label_mismatch")
        require(labels.get("org.opencontainers.image.revision") == plan.peer["image_revision"],
                "peer_revision_label_mismatch")
        image_id = docker("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()
        env_file = work / "peer.env"
        with env_file.open("x", encoding="utf-8") as stream:
            os.chmod(env_file, 0o600)
            stream.write("\n".join((
                f"HOMEASSISTANT_URL={core_url}", f"HOMEASSISTANT_TOKEN={token}",
                "MCP_HOST=127.0.0.1", f"MCP_PORT={plan.peer_port}", f"MCP_SECRET_PATH={SECRET_PATH}",
                "HA_MCP_DISABLE_SETTINGS_UI=true",
                # 9.1 app options from the lane contract (section B).
                "ENABLE_TOOL_SEARCH=true", "ENABLE_BETA_FEATURES=true",
                "HAMCP_ENABLE_FILESYSTEM_TOOLS=true", "READ_ONLY_MODE=false")) + "\n")
        try:
            docker("run", "-d", "--name", plan.identity + "-peer", "--network", "host", *common,
                   "--memory", "1g", "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/home/mcpuser/.ha-mcp",
                   "--env-file", str(env_file), image, "ha-mcp-web", timeout=120)
        finally:
            env_file.unlink(missing_ok=True)
        require(docker("inspect", "--format", "{{.Image}}", plan.identity + "-peer").stdout.strip() == image_id,
                "peer_running_image_mismatch")
        plan.evidence["peer_image_id"] = image_id
        await wait_http(plan.endpoint)
        package = docker("exec", plan.identity + "-peer", "python", "-c",
                         "from importlib.metadata import version; print(version('ha-mcp'))").stdout.strip()
        require(package == UPSTREAM_VERSION, "peer_package_version_mismatch")

        # Engineering in process: strict full-catalog admission, then typed controls.
        gateway = UpstreamReadGateway()
        gateway.configure(configured, core_runtime=core)
        health = await gateway.initialize(FastMCP("typed91-disposable"))
        plan.evidence["read_admission"] = health.get("admission_status")
        require(health.get("admission_status") == "admitted_exact", "read_gateway_not_admitted_exact")
        observer = Observer(plan.evidence)
        audit = AuditLogger(configured.audit_path, "synthetic-typed91-access")
        power_provider = PowerProvider.configured(configured, gateway)
        fan_provider = FanProvider.configured(configured, gateway)
        helper_provider = HelperProvider.configured(configured, gateway)
        for provider, name in ((power_provider, "power"), (fan_provider, "fan"), (helper_provider, "helper")):
            observer.wrap(provider, name)
        power = PowerService(str(work / "power"), power_provider, PowerCoreAuthority(core), audit=audit)
        fan = FanService(str(work / "fan"), fan_provider, FanCoreAuthority(core), audit=audit)
        helper = HelperService(str(work / "helper"), helper_provider, HelperCoreAuthority(core), audit=audit)
        telemetry, context = begin_request("typed91-" + arch)
        await guarded("power", plan, power_control(plan, power, rest, telemetry, observer))
        await guarded("fan", plan, fan_control(plan, fan, rest, telemetry, observer))
        await guarded("helper", plan, helper_control(plan, helper, rest, observer))
        plan.evidence["upstream_exchanges"] = len(observer.exchanges)
        require(all(item["complete"] for item in observer.exchanges), "upstream_exchange_incomplete")
    finally:
        if context is not None:
            from ha_mcp_engineering.request_context import end_request as _end
            _end(context)
        for service in (power, fan, helper):
            if service is not None:
                try:
                    await service.close()
                except Exception:
                    pass
        if gateway is not None and getattr(gateway, "_transport", None) is not None:
            try:
                await gateway._transport.aclose()
            except Exception:
                pass
        if core is not None:
            monitor = getattr(core, "_connection_monitor_task", None)
            try:
                core.request_reconciliation(connection_changed=True)
                if monitor is not None:
                    await asyncio.wait_for(asyncio.gather(monitor, return_exceptions=True), timeout=10)
            except Exception:
                pass
        cleanup(plan, config if config.exists() else None)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
