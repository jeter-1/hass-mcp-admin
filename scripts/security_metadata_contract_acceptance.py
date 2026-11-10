"""HAMCP-134 security_metadata disposable acceptance; never a production entry point.

Called only by core_security_metadata_local after it owns and verifies the Core
container. The real Engineering SecurityMetadataService, SecurityMetadataReader
and CoreRuntime are used against exact Core 2026.11.0; no ha-mcp container.
The two signatures below use a fresh in-memory test key, never publication keys.
The harness's own raw reads (independent values and before/after snapshots)
never pass through Engineering and are all reads; the harness writes nothing.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
import aiohttp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from alarmo_inspection_contract_acceptance import close_disposable_authority
from prepare_core_release_registry import prepare_candidate, sign_candidate
from ha_mcp_engineering.ha_core_readmission import CoreRuntime
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_SECURITY_METADATA_PROFILES
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.security_metadata import (
    AGENT_LIST, AUTH_LIST, CURRENT_USER, EXPOSE_NEW_GET, GET_CONFIG, GET_PANELS, HTTP_CONFIG,
    KNOWN_ASSISTANTS, NEVER_SENT_COMMANDS, NEVER_SENT_PREFIXES, OUTPUT_BYTES, PIPELINE_LIST,
    SECTIONS, SecurityMetadataError, SecurityMetadataReader, SecurityMetadataService,
    read_plan, validate_arguments)
from ha_mcp_engineering.signed_registry import canonical_json

import lane_failure_diagnostics as diagnostics

CAPABILITY = "core.security_metadata_read"
GROUP = "security_metadata"
REFERENCE_KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
CALLER = "hamcp134-disposable-admin"
# The lane's Engineering settings name HA_URL/HA_TOKEN, exactly as an
# alternative (non-Supervisor) deployment would; never the Supervisor topology.
TOPOLOGY = ("ha_url_env", "ha_token")
FULL_LEDGER = [CURRENT_USER, AUTH_LIST, HTTP_CONFIG, GET_CONFIG, EXPOSE_NEW_GET, EXPOSE_NEW_GET,
               EXPOSE_NEW_GET, PIPELINE_LIST, AGENT_LIST, GET_PANELS]
# Harness-only before/after snapshots: every registry or store the never-sent
# list protects. Each is a read; none is sent through Engineering.
SNAPSHOTS = ({"type": "config/auth/list"}, {"type": "http/config"},
             {"type": "config/entity_registry/list"}, {"type": "assist_pipeline/pipeline/list"},
             *({"type": EXPOSE_NEW_GET, "assistant": a} for a in KNOWN_ASSISTANTS))


def reference():
    profile, = (item for item in CORE_SECURITY_METADATA_PROFILES if item.capability_id == CAPABILITY)
    value = profile.to_mapping()
    return {key: value[key] for key in REFERENCE_KEYS}


class ObservedReader(SecurityMetadataReader):
    """The real reader; records only what it admitted and wrote."""

    def __init__(self, settings):
        super().__init__(settings)
        self.reads, self.admitted, self.sent = 0, [], []

    async def read(self, *, core_version, plan, authorize, on_dispatch=lambda _c: None,
                   on_sent=lambda _c: None):
        self.reads += 1

        def dispatch(command):
            on_dispatch(command)
            self.admitted.append(command)

        def sent(command):
            on_sent(command)
            self.sent.append(command)
        return await super().read(core_version=core_version, plan=plan, authorize=authorize,
                                  on_dispatch=dispatch, on_sent=sent)


class RawCore:
    """Harness-side authenticated WebSocket; one read command per connection."""

    def __init__(self, configured):
        self.url, self.token = configured.websocket_url, configured.ha_token

    async def command(self, payload):
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.ws_connect(self.url) as ws:
                assert (await ws.receive_json())["type"] == "auth_required"
                await ws.send_json({"type": "auth", "access_token": self.token})
                assert (await ws.receive_json())["type"] == "auth_ok"
                await ws.send_json({"id": 1, **payload})
                reply = await ws.receive_json()
                assert reply.get("type") == "result" and reply.get("id") == 1
                return reply

    async def result(self, payload):
        reply = await self.command(payload)
        assert reply["success"] is True
        return reply["result"]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


@diagnostics.checked("helper_setup", "helper_contract")
async def run(configured, entry, directory):
    """Run only security_metadata; caller owns startup, identity and teardown proof."""
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = b"Disposable security-metadata test authority; not registry publication authority"
    old = entry["capabilities"]
    assert len(old) == 32 and CAPABILITY not in {p["capability_id"] for p in old}
    runtimes = []
    raw = RawCore(configured)

    @diagnostics.checked("helper_setup", "authority_setup")
    async def authority(refs, name):
        test_entry = {**entry, "capabilities": refs, "evidence_sha256": hashlib.sha256(evidence).hexdigest()}
        candidate = canonical_json(prepare_candidate(entry=test_entry, evidence=evidence, previous=None,
                                                      public_key=key.public_key(), now=now))
        payload = sign_candidate(candidate, expected_sha256=hashlib.sha256(candidate).hexdigest(),
                                 previous=None, key=key, now=now)

        async def fetch(_url, _maximum):
            return payload
        registry = CoreReleaseRegistry(enabled=True, public_key=base64.b64encode(
            key.public_key().public_bytes_raw()).decode(), fetcher=fetch, cache_path=directory / (name + ".json"))
        assert await registry.refresh()
        runtime = CoreRuntime()
        runtimes.append(runtime)
        runtime.configure(configured, release_registry=registry)
        await runtime.reconcile_once("startup")
        assert runtime.current_observation.version == entry["version"]
        return runtime

    def compose(runtime):
        # The pending production composition (SecurityMetadataRuntime.configure)
        # with the lane's alternative-connection topology.
        reader = ObservedReader(configured)
        return SecurityMetadataService(reader, runtime, topology=TOPOLOGY, known_secrets=(
            configured.access_secret, configured.ha_token)), reader

    async def routed(runtime, service, arguments, caller=CALLER):
        """Mirror the MCP route: Core acquisition before the application.

        The tool's static route row is pending wiring (#418), so the lane
        acquires the profile itself, exactly as that row will.
        """
        telemetry, token = begin_request("hamcp134-security-metadata-route")
        telemetry.caller_id = caller
        commits = None
        lease = None
        try:
            lease = runtime.acquire((CAPABILITY,))
            if lease is None:
                return None

            def authorize():
                nonlocal commits
                if commits is not None:
                    return runtime.revalidate(lease, commits)
                commits = runtime.consume(lease)
                return commits is not None
            telemetry.core_dispatch_authorizer = authorize
            report = await service.metadata(validate_arguments(dict(arguments)))
            return report, dict(telemetry.audit_context["security_metadata_summary"])
        finally:
            if lease is not None:
                telemetry.core_dispatch_authorizer = lambda: False
                if commits is not None:
                    assert runtime.finish(commits)
                else:
                    runtime.release(lease)
            end_request(token)

    async def refused(call):
        try:
            await call
        except SecurityMetadataError as exc:
            return exc
        raise AssertionError("expected a security metadata refusal")

    @diagnostics.checked("readback", "state_readback")
    async def snapshot():
        return [digest(await raw.result(payload)) for payload in SNAPSHOTS]

    try:
        with diagnostics.check("readback", "state_readback"):
            raw_users = await raw.result({"type": AUTH_LIST})
            raw_current = await raw.result({"type": CURRENT_USER})
            raw_http = await raw.result({"type": HTTP_CONFIG})
            raw_config = await raw.result({"type": GET_CONFIG})
            raw_expose = {a: (await raw.result({"type": EXPOSE_NEW_GET, "assistant": a}))["expose_new"]
                          for a in KNOWN_ASSISTANTS}
            raw_pipelines = await raw.result({"type": PIPELINE_LIST})
            raw_agents = await raw.result({"type": AGENT_LIST})
            raw_panels = await raw.result({"type": GET_PANELS})
            before = await snapshot()
        assert type(raw_users) is list and raw_users and type(raw_current) is dict

        with diagnostics.check("operation", "negative_controls"):
            # Predecessor seq-17 shape (32 refs): acquisition refuses before the
            # application, and the service itself refuses with zero reads.
            predecessor = await authority(old, "predecessor")
            assert not predecessor.route_status((CAPABILITY,))["available"]
            withheld, withheld_reader = compose(predecessor)
            assert await routed(predecessor, withheld, {}) is None
            telemetry, token = begin_request("hamcp134-security-metadata-direct")
            telemetry.caller_id = CALLER
            try:
                exc = await refused(withheld.metadata(validate_arguments({})))
            finally:
                end_request(token)
            assert exc.reason == "authority_unavailable" and exc.details["fallback_occurred"] is False
            assert withheld_reader.reads == 0 and withheld_reader.sent == []

        after = await authority(old + [reference()], "extended")
        assert after.route_status((CAPABILITY,))["available"]
        service, reader = compose(after)
        with diagnostics.check("operation", "operation_dispatch"):
            report, audit = await routed(after, service, {})
            assert reader.reads == 1 and reader.admitted == reader.sent == FULL_LEDGER
            assert reader.sent == [payload["type"] for payload in read_plan(validate_arguments({}))]
            assert report["commands_sent"] == len(FULL_LEDGER)
            assert audit["commands_sent"] == FULL_LEDGER and audit["result"] == report["completeness"]
            assert report["core_version"] == entry["version"]

        with diagnostics.check("readback", "value_assertions"):
            text = json.dumps(report, sort_keys=True, ensure_ascii=False)
            assert len(text.encode()) <= OUTPUT_BYTES
            sections, coverage = report["sections"], report["coverage"]
            assert report["requested_sections"] == list(SECTIONS)
            assert report["connection_identity"] == {
                "kind": "other_connection_user", "is_admin": True,
                "basis": "alternative_connection_configuration"}
            # Users: an exact count and per-user flags; never an id, name or username.
            assert coverage["users"]["status"] == "observed"
            assert sections["users"]["user_count"] == len(raw_users) == len(sections["users"]["users"])
            for record in raw_users:
                # System-generated display names are Core constants, not personal
                # data, and may coincide with other Core labels; ids and
                # usernames are checked for every user.
                fields = ("id", "username") if record.get("system_generated") else ("id", "name", "username")
                for field in fields:
                    value = record.get(field)
                    if type(value) is str and len(value) >= 4:
                        assert value not in text, field
            assert raw_current["id"] not in text
            assert configured.ha_token not in text and configured.access_secret not in text
            assert sum(1 for user in sections["users"]["users"] if user["is_owner"] is True) == sum(
                1 for record in raw_users if record.get("is_owner") is True)
            # MFA: module ids/names only; enrolment is withheld off the Supervisor identity.
            assert coverage["mfa"] == {"status": "partial", "commands": [CURRENT_USER],
                                       "reason_code": "connection_identity_not_supervisor"}
            assert [m["id"] for m in sections["mfa"]["modules"]] == [
                m["id"] for m in raw_current["mfa_modules"]]
            assert sections["mfa"]["connection_user_enrolment"] == "not_reported"
            assert all("enabled" not in m and "service_user_enrolled" not in m
                       for m in sections["mfa"]["modules"])
            # HTTP: the active slot is the one Core names, never substituted.
            http = sections["http_boundary"]
            assert coverage["http_boundary"]["status"] in {"observed", "partial"}
            assert http["active_config_type"] == raw_http.get("active_config_type")
            assert http["configured"]["label"] == "configured_not_necessarily_active"
            # URLs: presence agrees with Core; no URL text is ever echoed.
            urls = sections["urls"]
            assert coverage["urls"]["status"] in {"observed", "partial"}
            for key in ("external_url", "internal_url"):
                present = raw_config.get(key) not in (None, "")
                assert key in raw_config and urls[key]["present"] is present
                if present:
                    assert raw_config[key] not in text
            # Assist: expose-new defaults equal Core; counts equal Core.
            assist = sections["assist"]
            assert coverage["assist"]["status"] == "observed"
            assert assist["expose_new_entities"] == raw_expose
            assert len(assist["pipelines"]) == len(raw_pipelines["pipelines"])
            assert len(assist["agents"]) == len(raw_agents["agents"])
            # Panels: an exact count and the listing value only.
            assert coverage["panels"]["status"] == "observed"
            assert sections["panels"]["panel_count"] == len(raw_panels)
            assert report["omissions"] and report["permission_limits"]

        with diagnostics.check("operation", "negative_controls"):
            # sections=["mfa"]: identity from topology plus auth/current_user only.
            count = len(reader.sent)
            mfa_only, mfa_audit = await routed(after, service, {"sections": ["mfa"]})
            assert reader.sent[count:] == [CURRENT_USER] and mfa_audit["commands_sent"] == [CURRENT_USER]
            assert AUTH_LIST not in reader.sent[count:]
            assert all(mfa_only["coverage"][name]["reason_code"] == "not_requested"
                       for name in SECTIONS if name != "mfa")
            # Anonymous caller: refused before any read.
            count = reader.reads
            exc = await refused(routed(after, service, {}, caller="anonymous"))
            assert exc.reason == "authority_unavailable" and reader.reads == count
            # Wrong Core identity: refused at auth_ok before any command is admitted.
            refused_versions = {}
            for version in ("2026.10.0", "2026.10.1", "2026.10.3"):
                direct = ObservedReader(configured)
                exc = await refused(direct.read(core_version=version,
                                                plan=read_plan(validate_arguments({})),
                                                authorize=lambda: None))
                assert exc.reason == "identity_mismatch" and direct.admitted == direct.sent == []
                refused_versions[version] = exc.reason
            # Out-of-contract arguments never reach Core.
            invalid = ({"sections": []}, {"sections": ["users", "users"]}, {"sections": ["exposure"]},
                       {"sections": "users"}, {"extra": 1})
            for arguments in invalid:
                try:
                    validate_arguments(arguments)
                except SecurityMetadataError as exc:
                    assert exc.reason == "invalid_arguments"
                else:
                    raise AssertionError("invalid arguments must refuse")
            # Never-sent: no excluded command or Supervisor prefix ever left the reader.
            assert not set(reader.sent) & set(NEVER_SENT_COMMANDS)
            assert not any(command.startswith(NEVER_SENT_PREFIXES) for command in reader.sent)
            assert after.health_snapshot()["fallback_count"] == 0
        with diagnostics.check("readback", "state_readback"):
            unchanged = await snapshot() == before
            assert unchanged
        return {"result": "PASS", "group": GROUP, "profile": reference(),
                "authority_transition": [32, 33], "predecessor_reads": 0,
                "commands_sent": list(reader.sent), "full_ledger": list(FULL_LEDGER),
                "connection_identity": report["connection_identity"],
                "completeness": report["completeness"],
                "coverage": {name: {k: report["coverage"][name][k] for k in ("status", "reason_code")}
                             for name in SECTIONS},
                "item_counts": dict(report["item_counts"]),
                "raw_counts": {"users": len(raw_users), "pipelines": len(raw_pipelines["pipelines"]),
                               "agents": len(raw_agents["agents"]), "panels": len(raw_panels)},
                "http_active_config_type": raw_http.get("active_config_type"),
                "output_bytes": len(text.encode()),
                "redaction": report["redaction"],
                "snapshots_unchanged": len(SNAPSHOTS),
                "refusals": {"predecessor": "authority_unavailable", "anonymous": "authority_unavailable",
                             "wrong_core_version": refused_versions, "invalid_arguments": len(invalid)},
                "mfa_only_commands": [CURRENT_USER],
                "never_sent_commands_sent": 0, "fallback_count": 0, "production_authority": False}
    finally:
        with diagnostics.check("cleanup", "service_close"):
            for runtime in runtimes:
                await close_disposable_authority(runtime)
