"""HAMCP-290 marketplace_read disposable acceptance; never a production entry point.

Called only by core_marketplace_read_local after it owns and verifies the Core
container. The real Engineering MarketplaceInventoryService, MarketplaceReader
and CoreRuntime are used against exact Core 2026.11.0; no ha-mcp container.
The two signatures below use a fresh in-memory test key, never publication keys.
The harness's own raw reads (store readiness, independent counts, and disabling
only the disposable Marketplace config entry) never pass through Engineering.

The install fixture (HAMCP-290 follow-up) installs exactly one public theme
repository into the disposable Core only, through the harness's own raw
WebSocket, never through Engineering. It refuses when the disposable
Marketplace has a GitHub account connected (no token is used or supplied), and
it adds no network configuration: Core downloads anonymously over the lane's
existing bridge egress. Files land in the disposable /config, which cleanup
removes and reads back as absent.
"""
import asyncio
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
from ha_mcp_engineering.ha_core_readmission.profiles import CORE_MARKETPLACE_PROFILES
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_core_readmission.routes import (
    MARKETPLACE_TOOL, static_tool_requirements, static_tool_target)
from ha_mcp_engineering.marketplace_inventory import (
    BACKEND_MARKETPLACE, HACS_LIST_COMMAND, MARKETPLACE_LIST_COMMAND, MarketplaceError,
    MarketplaceInventoryService, MarketplaceReader, validate_arguments)
from ha_mcp_engineering.request_context import begin_request, end_request
from ha_mcp_engineering.signed_registry import canonical_json

import lane_failure_diagnostics as diagnostics

CAPABILITY = "core.marketplace_read"
REFERENCE_KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
CALLER = "hamcp290-disposable-admin"
# Harness-only raw client bound: Phase 1 saw a ~4.26 MB list, above aiohttp's
# 4 MiB default. Engineering's own reader keeps its reviewed 8 MiB frame bound.
HARNESS_FRAME_BYTES = 16 * 1024 * 1024
STORE_READY_SECONDS = 600
# Install fixture: exactly one inert theme (YAML only, no code Core imports).
# Only these named repositories may be installed, the first one present in the
# store. With neither present the fixture fails and the lane withholds its
# receipt. One install attempt, never more.
INSTALL_CANDIDATES = ("JuanMTech/google_light_theme", "catppuccin/home-assistant")
INSTALL_CATEGORY = "theme"
INSTALL_COMMANDS = ("marketplace/warning/accept", "marketplace/repository/install")
INSTALL_SECONDS = 300
INSTALLED_POLL_SECONDS = 120
# The reviewed projection, restated independently of the runtime constants.
PROJECTED = ("id", "full_name", "name", "category", "domain", "installed_version",
             "available_version", "pending_upgrade", "custom", "description")
TEXT_BOUNDS = {"name": 200, "installed_version": 64, "available_version": 64, "description": 300}
TRUNCATED = "...<truncated>"


# Code-owned reasons; lane_failure_diagnostics exports exactly these messages.
FIXTURE_REASONS = (
    "github_account_connected", "disposable_core_not_fresh", "install_candidate_missing",
    "warning_accept_failed", "install_failed", "installed_record_missing", "more_than_one_installed",
    "installed_record_duplicated", "installed_record_unprojected", "installed_record_identity_changed",
    "installed_record_flags_changed", "installed_record_not_default_catalog", "installed_version_missing",
    "installed_record_unbounded", "installed_record_text_changed", "secret_in_page",
    "redaction_canary_unavailable", "redaction_canary_leaked", "redaction_canary_undisclosed",
    "redaction_canary_changed_records")


class FixtureError(AssertionError):
    """A reason-coded fixture or projection failure; never carries upstream text."""

    def __init__(self, reason):
        if reason not in FIXTURE_REASONS:
            raise ValueError("unknown fixture reason")
        super().__init__(reason)
        self.reason = reason


def reference():
    profile, = (item for item in CORE_MARKETPLACE_PROFILES if item.capability_id == CAPABILITY)
    value = profile.to_mapping()
    return {key: value[key] for key in REFERENCE_KEYS}


class ObservedReader(MarketplaceReader):
    """The real reader; records only what it admitted and wrote."""

    def __init__(self, settings):
        super().__init__(settings)
        self.reads, self.admitted, self.sent = 0, [], []

    async def read(self, *, core_version, authorize, on_dispatch=lambda _c: None, on_sent=lambda _c: None):
        self.reads += 1

        def dispatch(command):
            on_dispatch(command)
            self.admitted.append(command)

        def sent(command):
            on_sent(command)
            self.sent.append(command)
        return await super().read(core_version=core_version, authorize=authorize,
                                  on_dispatch=dispatch, on_sent=sent)


class RawCore:
    """Harness-side authenticated WebSocket; one command per connection."""

    def __init__(self, configured):
        self.url, self.token = configured.websocket_url, configured.ha_token

    async def command(self, payload, *, seconds=60):
        timeout = aiohttp.ClientTimeout(total=seconds)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.ws_connect(self.url, max_msg_size=HARNESS_FRAME_BYTES) as ws:
                assert (await ws.receive_json())["type"] == "auth_required"
                await ws.send_json({"type": "auth", "access_token": self.token})
                assert (await ws.receive_json())["type"] == "auth_ok"
                await ws.send_json({"id": 1, **payload})
                reply = await ws.receive_json()
                assert reply.get("type") == "result" and reply.get("id") == 1
                return reply


def observed_fallbacks(*readers):
    """Count the commands the readers admitted or wrote other than the Marketplace list.

    A command admitted and then written is one command; one admitted but never
    written still counts.
    """
    def others(commands):
        return sum(1 for command in commands if command != MARKETPLACE_LIST_COMMAND)
    return sum(max(others(reader.admitted), others(reader.sent)) for reader in readers)


def choose_candidate(store):
    """Return the store record of the one named repository the fixture installs."""
    themes = [record for record in store if type(record) is dict
              and record.get("category") == INSTALL_CATEGORY and record.get("installed") is False
              and record.get("custom") is False]
    for name in INSTALL_CANDIDATES:
        for record in themes:
            if record.get("full_name") == name:
                return record
    raise FixtureError("install_candidate_missing")


async def install_fixture(raw, store, *, sleep=asyncio.sleep):
    """Install exactly one repository into the disposable Core; return its raw record.

    Refuses a connected GitHub account (a token), a non-fresh Core, a failed
    install, and a record that never reads back installed.
    """
    info = await raw.command({"type": "marketplace/info"})
    if info.get("success") is not True or info["result"].get("github_connected") is not False:
        raise FixtureError("github_account_connected")
    if any(type(record) is dict and record.get("installed") is True for record in store):
        raise FixtureError("disposable_core_not_fresh")
    candidate = choose_candidate(store)
    repository_id = str(candidate["id"])
    accepted = await raw.command({"type": INSTALL_COMMANDS[0]})
    if accepted.get("success") is not True:
        raise FixtureError("warning_accept_failed")
    installed = await raw.command({"type": INSTALL_COMMANDS[1], "repository": repository_id},
                                  seconds=INSTALL_SECONDS)
    if installed.get("success") is not True:
        # Never Core's message: it can carry upstream text. Core's own log has it.
        raise FixtureError("install_failed")
    for _ in range(INSTALLED_POLL_SECONDS // 5):
        reply = await raw.command({"type": MARKETPLACE_LIST_COMMAND})
        if reply.get("success") is True and type(reply.get("result")) is list:
            matches = [record for record in reply["result"]
                       if type(record) is dict and str(record.get("id")) == repository_id]
            if (len(matches) == 1 and matches[0].get("installed") is True
                    and type(matches[0].get("installed_version")) is str and matches[0]["installed_version"]):
                others = [record for record in reply["result"] if type(record) is dict
                          and record.get("installed") is True and str(record.get("id")) != repository_id]
                if others:
                    raise FixtureError("more_than_one_installed")
                return matches[0], reply["result"]
        await sleep(5)
    raise FixtureError("installed_record_missing")


def check_installed_record(page, raw_record, known_secrets):
    """The routed page must carry the real installed record, projected and screened.

    Missing, duplicated or unprojected (raw Core fields, changed identity,
    unbounded or silently altered text, a leaked secret) records fail.
    """
    repository_id = str(raw_record["id"])
    matches = [record for record in page["results"] if record.get("id") == repository_id]
    if not matches:
        raise FixtureError("installed_record_missing")
    if len(matches) != 1:
        raise FixtureError("installed_record_duplicated")
    record, = matches
    if set(record) != set(PROJECTED):
        raise FixtureError("installed_record_unprojected")
    domain = raw_record.get("domain") or None
    if (record["full_name"], record["category"], record["domain"]) != (
            raw_record["full_name"], raw_record["category"], domain):
        raise FixtureError("installed_record_identity_changed")
    if (record["pending_upgrade"], record["custom"]) != (raw_record["pending_upgrade"], raw_record["custom"]):
        raise FixtureError("installed_record_flags_changed")
    if record["custom"] is not False:
        raise FixtureError("installed_record_not_default_catalog")
    if type(record["installed_version"]) is not str or not record["installed_version"]:
        raise FixtureError("installed_version_missing")
    sanitized = "sanitized_text" in page["partial_reasons"]
    text = {}
    for key, bound in TEXT_BOUNDS.items():
        value, source = record[key], raw_record.get(key)
        if value is None or source is None:
            if value is not None or source not in (None, ""):
                raise FixtureError("installed_record_text_changed")
            text[key] = "absent"
            continue
        if type(value) is not str or len(value) > bound + len(TRUNCATED):
            raise FixtureError("installed_record_unbounded")
        if len(value) > bound and not value.endswith(TRUNCATED):
            raise FixtureError("installed_record_unbounded")
        if value == source:
            if len(source) > bound:
                raise FixtureError("installed_record_unbounded")
            text[key] = "equal"
        elif sanitized:
            text[key] = "screened"
        else:
            raise FixtureError("installed_record_text_changed")
    rendered = json.dumps(page, ensure_ascii=False)
    if any(secret and secret in rendered for secret in known_secrets):
        raise FixtureError("secret_in_page")
    return {"id": repository_id, "full_name": record["full_name"], "category": record["category"],
            "installed_version": record["installed_version"], "fields": sorted(record), "text": text}


def redaction_canary(raw_record):
    """A string from the real record that the canary service must screen out."""
    for key in ("description", "name"):
        value = raw_record.get(key)
        if type(value) is str and len(value.strip()) >= 4:
            return key, value
    raise FixtureError("redaction_canary_unavailable")


@diagnostics.checked("helper_setup", "helper_contract")
async def run(configured, entry, directory):
    """Run only marketplace_read; caller owns startup, identity and teardown proof."""
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = b"Disposable marketplace-read test authority; not registry publication authority"
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

    def compose(runtime, *extra_secrets):
        # The production composition (MarketplaceInventoryRuntime.configure).
        reader = ObservedReader(configured)
        return MarketplaceInventoryService(reader, runtime, known_secrets=(
            configured.access_secret, configured.ha_token, *extra_secrets)), reader

    async def routed(runtime, service, arguments, caller=CALLER):
        """Mirror the MCP route: Core acquisition before the application."""
        telemetry, token = begin_request("hamcp290-marketplace-route")
        telemetry.caller_id = caller
        commits = None
        lease = None
        try:
            lease = runtime.acquire(static_tool_requirements(MARKETPLACE_TOOL, arguments),
                                    target=static_tool_target(MARKETPLACE_TOOL, arguments))
            if lease is None:
                return None

            def authorize():
                nonlocal commits
                if commits is not None:
                    return runtime.revalidate(lease, commits)
                commits = runtime.consume(lease)
                return commits is not None
            telemetry.core_dispatch_authorizer = authorize
            return await service.inventory(validate_arguments(dict(arguments)))
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
        except MarketplaceError as exc:
            return exc
        raise AssertionError("expected a Marketplace refusal")

    @diagnostics.checked("readback", "state_readback")
    async def raw_store():
        reply = await raw.command({"type": MARKETPLACE_LIST_COMMAND})
        assert reply["success"] is True and type(reply["result"]) is list
        return reply["result"]

    @diagnostics.checked("core_boot", "runtime_ready")
    async def store_ready():
        # Core lists only fetched repositories; wait for the first store fetch.
        for _ in range(STORE_READY_SECONDS // 10):
            info = await raw.command({"type": "marketplace/info"})
            if info.get("success") is True and info["result"].get("stage") == "running":
                store = await raw_store()
                if store:
                    return info["result"], store
            await asyncio.sleep(10)
        raise RuntimeError("Disposable Marketplace store did not finish its first fetch")

    try:
        info, fresh = await store_ready()
        with diagnostics.check("operation", "install_fixture"):
            raw_record, baseline = await install_fixture(raw, fresh)
        store_sha256 = hashlib.sha256(json.dumps(baseline, sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=False).encode()).hexdigest()
        raw_installed = sum(1 for record in baseline if record.get("installed") is True)

        with diagnostics.check("operation", "negative_controls"):
            # Predecessor seq-17 shape (32 refs): the route refuses before the
            # application, and the service itself refuses with zero reads.
            before = await authority(old, "predecessor")
            assert not before.route_status((CAPABILITY,))["available"]
            withheld, withheld_reader = compose(before)
            assert await routed(before, withheld, {}) is None
            telemetry, token = begin_request("hamcp290-marketplace-direct")
            telemetry.caller_id = CALLER
            try:
                exc = await refused(withheld.inventory(validate_arguments({})))
            finally:
                end_request(token)
            assert exc.reason == "authority_unavailable" and exc.fallback_occurred is False
            assert withheld_reader.reads == 0 and withheld_reader.sent == []

        after = await authority(old + [reference()], "extended")
        assert after.route_status((CAPABILITY,))["available"]
        service, reader = compose(after)
        with diagnostics.check("operation", "operation_dispatch"):
            page = await routed(after, service, {"limit": 50})
            assert page is not None and page["backend"] == BACKEND_MARKETPLACE
            assert page["fallback_occurred"] is False and page["commands_sent"] == 1
            assert reader.reads == 1 and reader.admitted == reader.sent == [MARKETPLACE_LIST_COMMAND]
        with diagnostics.check("readback", "value_assertions"):
            # Independent raw count; the store may change between the two reads,
            # so compare against a raw read bracketing the routed one.
            later = await raw_store()
            later_installed = sum(1 for record in later if record.get("installed") is True)
            assert page["installed_total"] in {raw_installed, later_installed}
            assert page["count"] == min(50, page["total_matches"]) and page["offset"] == 0
            assert all(set(r) == {"id", "full_name", "name", "category", "domain", "installed_version",
                                  "available_version", "pending_upgrade", "custom", "description"}
                       for r in page["results"])
            assert page["inventory_fingerprint"].startswith("sha256:")
            assert page["installed_total"] >= 1 and page["count"] >= 1
            installed_record = check_installed_record(
                page, raw_record, (configured.access_secret, configured.ha_token))
        with diagnostics.check("readback", "redaction_canary"):
            # The same real record through a service that treats one of its own
            # strings as a secret: the page must screen it and say so.
            canary_field, canary = redaction_canary(raw_record)
            canary_service, canary_reader = compose(after, canary)
            screened = await routed(after, canary_service, {"limit": 50})
            if screened is None or canary in json.dumps(screened, ensure_ascii=False):
                raise FixtureError("redaction_canary_leaked")
            if (screened["completeness"] != "partial" or "sanitized_text" not in screened["partial_reasons"]
                    or not screened["redaction"]):
                raise FixtureError("redaction_canary_undisclosed")
            if [r["id"] for r in screened["results"]] != [r["id"] for r in page["results"]]:
                raise FixtureError("redaction_canary_changed_records")
            assert canary_reader.sent == [MARKETPLACE_LIST_COMMAND]

        with diagnostics.check("operation", "negative_controls"):
            # Anonymous caller: refused before any read.
            count = reader.reads
            exc = await refused(routed(after, service, {}, caller="anonymous"))
            assert exc.reason == "authority_unavailable" and reader.reads == count
            # Wrong Core identity: refused at auth_ok before any command is admitted.
            direct = ObservedReader(configured)
            exc = await refused(direct.read(core_version="2026.10.3", authorize=lambda: None))
            assert exc.reason == "identity_mismatch" and exc.fallback_occurred is False
            assert direct.admitted == direct.sent == []
            # Out-of-contract arguments never reach Core.
            for arguments in ({"limit": 51}, {"category": "appdaemon"}, {"offset": -1}, {"extra": 1}):
                try:
                    validate_arguments(arguments)
                except MarketplaceError as exc:
                    assert exc.reason == "invalid_arguments"
                else:
                    raise AssertionError("invalid arguments must refuse")
            # Disable only the disposable Marketplace entry: native not_loaded,
            # never a HACS fallback, exactly one command sent.
            entries = await raw.command({"type": "config_entries/get", "domain": "marketplace"})
            assert entries["success"] is True and len(entries["result"]) == 1
            disabled = await raw.command({"type": "config_entries/disable",
                                          "entry_id": entries["result"][0]["entry_id"], "disabled_by": "user"})
            assert disabled["success"] is True
            unloaded = await raw.command({"type": MARKETPLACE_LIST_COMMAND})
            assert unloaded["success"] is False and unloaded["error"]["code"] == "not_loaded"
            sent = len(reader.sent)
            exc = await refused(routed(after, service, {}))
            assert exc.reason == "marketplace_not_loaded" and exc.fallback_occurred is False
            assert reader.sent[sent:] == [MARKETPLACE_LIST_COMMAND] and HACS_LIST_COMMAND not in reader.sent
            assert after.health_snapshot()["fallback_count"] == 0
            # Every reader this lane built, over every read it made.
            fallback_count = observed_fallbacks(withheld_reader, reader, canary_reader, direct)
            assert fallback_count == 0
        return {"result": "PASS", "group": "marketplace_read", "profile": reference(),
                "authority_transition": [32, 33], "predecessor_reads": 0,
                "marketplace_stage": info["stage"], "store_records": len(baseline),
                "fresh_installed": 0, "install_fixture": {
                    "repository": raw_record["full_name"], "id": str(raw_record["id"]),
                    "category": raw_record["category"], "installed_version": raw_record["installed_version"],
                    "candidates": list(INSTALL_CANDIDATES), "commands": list(INSTALL_COMMANDS),
                    "github_connected": False, "disposable_only": True, "network_change": False},
                "installed_record": installed_record,
                "redaction_canary": {"field": canary_field, "completeness": screened["completeness"],
                                     "partial_reasons": screened["partial_reasons"], "leaked": False},
                "store_sha256": store_sha256, "raw_installed": raw_installed,
                "inventory": {key: page[key] for key in ("backend", "installed_total", "total_matches", "count",
                                                         "completeness", "partial_reasons", "fallback_occurred",
                                                         "commands_sent", "inventory_fingerprint")},
                "commands_sent": list(reader.sent),
                "refusals": {"predecessor": "authority_unavailable", "anonymous": "authority_unavailable",
                             "wrong_core_version": "identity_mismatch", "invalid_arguments": 4,
                             "entry_disabled": "marketplace_not_loaded"},
                "hacs_commands_sent": 0, "fallback_count": fallback_count, "production_authority": False}
    finally:
        with diagnostics.check("cleanup", "service_close"):
            for runtime in runtimes:
                await close_disposable_authority(runtime)
