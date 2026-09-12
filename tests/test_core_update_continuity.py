from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from ha_mcp_engineering.ha_core_readmission import CoreRuntime, CORE_IDENTITY, compiled_exact_authority
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry, CORE_REGISTRY_URL
from ha_mcp_engineering.ha_core_readmission.registry_models import CoreRegistryEnvelope
from ha_mcp_engineering.ha_mcp_readmission.registry import SignedReleaseRegistry, ReleaseRegistryOperationalError, MAX_CACHE_BYTES
from ha_mcp_engineering.signed_registry import canonical_json, RegistryValidationError
from ha_mcp_engineering.signed_registry.canonical import sha256_digest
from core_registry_fixtures import CoreSigner, ProjectedCoreSource, core_entry, core_revocation, NOW


class CoreContinuityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "core.json"
        self.signer = CoreSigner()
        self.clock = NOW
        self.raw = self.signer.journal_raw()
        self.fetches = []

        async def fetch(url, maximum):
            self.fetches.append((url, maximum))
            self.assertEqual(url, CORE_REGISTRY_URL)
            if isinstance(self.raw, Exception):
                raise self.raw
            return self.raw

        self.registry = CoreReleaseRegistry(enabled=True, public_key=self.signer.public_key_base64,
                                            cache_path=self.cache, fetcher=fetch, now=lambda: self.clock)

    async def runtime(self, version="2026.9.2"):
        source = ProjectedCoreSource(self.registry, version)
        runtime = CoreRuntime()
        runtime.configure(object(), source=source, release_registry=self.registry)
        await runtime.reconcile_once("startup")
        return runtime, source

    def assert_admitted(self, runtime, count=17):
        health = runtime.health_snapshot()
        self.assertEqual(sum(p["disposition"].startswith("admitted_")
                             for p in health["authority_profiles"]), count, health)
        self.assertEqual(health["fallback_count"], 0)

    def next_journal(self, *, entries=None, revocations=None):
        old = json.loads(self.raw)
        tip = CoreRegistryEnvelope.from_mapping(old["envelopes"][-1])
        envelope = self.signer.raw(sequence=tip.sequence + 1,
                                   previous_registry_sha256=tip.content_digest,
                                   generated_at=NOW,
                                   entries=entries if entries is not None else [core_entry()],
                                   revocations=revocations or [])
        return self.signer.journal_raw(envelopes=[*old["envelopes"], envelope])

    async def test_signed_future_core_admits_existing_profiles_without_compiled_version(self):
        self.assertEqual(compiled_exact_authority("2026.9.2"), ())
        runtime, source = await self.runtime()
        self.assert_admitted(runtime)
        self.assertEqual(source.calls, 2)
        self.assertEqual(len(self.fetches), 1)
        self.assertTrue(all(p["authority_source"] == "verified_compatibility"
                            for p in runtime.health_snapshot()["authority_profiles"]))

    async def test_dependency_build_publishes_only_current_signed_core_evidence(self):
        from tests.test_dependency_build_authority import _Network
        from tests.test_ha_core_2026_9_integration import settings
        from ha_mcp_engineering.clients.rest import HomeAssistantRestClient
        from ha_mcp_engineering.clients.websocket import HomeAssistantWebSocketClient
        from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime
        core, _ = await self.runtime()
        network = _Network()
        network.payloads["/config"] = {"version": "2026.9.2"}
        dependency = DependencyAnalysisRuntime()
        configured = settings()
        dependency.configure(HomeAssistantRestClient(configured), HomeAssistantWebSocketClient(configured),
                             core_runtime=core)
        try:
            with patch("ha_mcp_engineering.clients.rest.aiohttp.ClientSession", side_effect=network.session):
                self.assertTrue(await asyncio.wait_for(dependency.start_prewarm(startup_delay_seconds=0), 3))
                index = dependency.require().index
                self.assertTrue(index.active_identity()["current"])
                self.assertEqual(index.generation, 1)
                self.clock += timedelta(days=2)
                self.assertFalse(core.route_status(("core.dependency_helper_planning",))["available"])
                self.assertFalse(index.active_identity()["current"])
                calls = len(network.calls)
                self.assertFalse(await dependency._authorized_prewarm(index.provider))
                self.assertEqual(len(network.calls), calls)
        finally:
            await dependency.shutdown()
        health = core.health_snapshot()
        for name in ("issued_lease_count", "active_commit_count", "fallback_count"):
            self.assertEqual(health[name], 0)

    async def test_signed_mutation_authority_keeps_target_single_use_and_revocation_fences(self):
        runtime, _ = await self.runtime()
        required = ("core.f3_mutation_verification", "core.governed_configuration_operation")
        authority = runtime.acquire(required, target={"target_type": "automation", "target_id": "one"})
        forged = replace(authority, target_fingerprint=runtime.target_fingerprint(
            {"target_type": "automation", "target_id": "two"}))
        self.assertIsNone(runtime.consume(forged))
        commits = runtime.consume(authority)
        self.assertIsNotNone(commits)
        self.assertIsNone(runtime.consume(authority))
        self.raw = self.next_journal(entries=[], revocations=[core_revocation()])
        self.assertTrue(await self.registry.refresh())
        self.assertFalse(runtime.revalidate(authority, commits))
        self.assertTrue(runtime.finish(commits))
        self.assertIsNone(runtime.acquire(required))
        self.assertEqual(runtime.health_snapshot()["active_commit_count"], 0)

    async def test_disposable_lane_harness_exercises_real_signature_and_projection(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from core_registry_contract_lane import configure_with_test_authority
        from tests.test_ha_core_2026_9_integration import settings
        runtime = CoreRuntime()
        configure = runtime.configure

        def synthetic_transport(configured, *, release_registry):
            configure(configured, release_registry=release_registry,
                      source=ProjectedCoreSource(release_registry))

        image = "ghcr.io/home-assistant/home-assistant:2026.9.2@sha256:a1bc133af84ee6505fe2c266d9805b7c75b780dfdc188edfee3b11e8f3cd8efe"
        with patch.object(runtime, "configure", side_effect=synthetic_transport):
            await configure_with_test_authority(runtime, settings(), cache_path=self.cache,
                                                expected_image=image)
        self.assert_admitted(runtime)
        with self.assertRaises(AssertionError):
            await configure_with_test_authority(runtime, settings(), cache_path=self.cache,
                                                expected_image="unbound")

    async def test_unknown_release_stays_withheld_without_signed_authority(self):
        runtime, _ = await self.runtime("2026.9.3")
        self.assert_admitted(runtime, 0)
        self.assertIsNone(runtime.acquire(("core.basic_rest_read",)))
        fetches = len(self.fetches)
        await runtime.reconcile_once()
        self.assertEqual(len(self.fetches), fetches)

    async def test_registry_failure_keeps_compiled_pairing_but_cannot_admit_future(self):
        self.raw = OSError("synthetic transport failure")
        runtime, source = await self.runtime("2026.9.1")
        self.assert_admitted(runtime)
        source.version = "2026.9.2"
        await runtime.reconcile_once()
        self.assert_admitted(runtime, 0)

    async def test_new_reviewed_renewal_restores_expired_authority_without_restart(self):
        runtime, source = await self.runtime()
        previous = CoreRegistryEnvelope.from_mapping(json.loads(self.raw)["envelopes"][-1])
        self.clock += timedelta(days=2)
        self.assertFalse(runtime.route_status(("core.basic_rest_read",))["available"])
        envelope = self.signer.raw(
            sequence=2, previous_registry_sha256=previous.content_digest,
            generated_at=self.clock, expires_at=self.clock + timedelta(days=1),
        )
        self.raw = self.signer.journal_raw(envelopes=[previous.to_mapping(), envelope])
        self.assertTrue(await self.registry.refresh())
        self.assertFalse(runtime.route_status(("core.basic_rest_read",))["available"])
        await runtime.reconcile_once()
        self.assert_admitted(runtime)
        self.assertEqual(source.version, "2026.9.2")

    def test_core_settings_require_own_valid_key_and_disabled_default_is_inert(self):
        from tests.test_ha_core_2026_9_integration import settings
        from ha_mcp_engineering.application import validate_settings
        from ha_mcp_engineering.errors import ConfigurationError
        valid = replace(settings(), access_secret="synthetic-access-secret-32-characters",
                        ha_core_release_registry_enabled=True,
                        ha_core_release_registry_public_key=self.signer.public_key_base64)
        validate_settings(valid)
        for invalid in ("", "bad-public-key", "A" * 42):
            with self.assertRaises(ConfigurationError):
                validate_settings(replace(valid, ha_core_release_registry_public_key=invalid))
        with self.assertRaises(ConfigurationError):
            validate_settings(replace(valid, ha_mcp_release_registry_public_key=self.signer.public_key_base64))
        default = CoreReleaseRegistry(enabled=False, public_key="not-used", cache_path=self.cache)
        self.assertFalse(default.refresh_due())
        self.assertEqual(default.selections("2026.9.2"), ())
        self.assertTrue(default.selections("2026.9.1"))

    async def test_bad_signature_and_oversized_payload_cannot_create_core_authority(self):
        for raw in (CoreSigner().journal_raw(), b" " * (MAX_CACHE_BYTES + 1)):
            self.raw = raw
            self.assertFalse(await self.registry.refresh())
            self.assertEqual(self.registry.selections("2026.9.2"), ())
        self.assertEqual(self.registry.snapshot()["last_failure_reason"], "registry_journal_oversized")

    async def test_actual_f3_guards_reprobe_and_stop_readback_on_signed_expiry(self):
        from tests.test_ha_core_2026_9_integration import _CoreBoundDashboardOperation
        from ha_mcp_engineering.f3_runtime.runtime import _CoreDispatchAuthorityGuard, _CoreVerificationAdapter
        from ha_mcp_engineering.request_context import current_telemetry
        runtime, source = await self.runtime()
        prepared = _CoreBoundDashboardOperation()
        guard = _CoreDispatchAuthorityGuard(runtime, datetime.now(timezone.utc).isoformat())
        authority = await guard.acquire(prepared, object())
        self.assertIsNotNone(authority)
        self.assertEqual(source.calls, 4)
        commits = guard.consume(authority)
        self.assertTrue(guard.revalidate(authority, commits))
        self.assertTrue(guard.finish(commits))
        reads = []
        owner = self

        class Adapter:
            capabilities = object()

            async def observe(self, _prepared, _dispatch):
                telemetry = current_telemetry()
                if telemetry.authorize_core_dispatch():
                    reads.append("first")
                owner.clock += timedelta(days=2)
                if telemetry.authorize_core_dispatch():
                    reads.append("forbidden_after_expiry")
                return "observation_interrupted"

        result = await _CoreVerificationAdapter(Adapter(), runtime).observe(prepared, object())
        self.assertEqual(result, "observation_interrupted")
        self.assertEqual(reads, ["first"])
        self.assertIsNone(await guard.acquire(prepared, object()))
        health = runtime.health_snapshot()
        for name in ("issued_lease_count", "active_commit_count", "fallback_count"):
            self.assertEqual(health[name], 0)

    async def test_future_device_routes_keep_exact_corrected_upstream_adapter(self):
        runtime, _ = await self.runtime()
        requirement = ("core.delegated_device_effective_area",)
        self.assertFalse(runtime.delegated_tool_available("ha_get_device", requirement, adapter_version="8.4.1"))
        self.assertIsNone(runtime.acquire(requirement, delegated_tool="ha_get_entity", delegated_adapter_version="8.4.1"))
        lease = runtime.acquire(requirement, delegated_tool="ha_get_device", delegated_adapter_version="8.4.3")
        self.assertIsNotNone(lease)
        commits = runtime.consume(lease)
        self.assertTrue(runtime.revalidate(lease, commits))
        self.assertTrue(runtime.finish(commits))
        self.assertIsNone(runtime.consume(lease))

    async def test_expiry_retires_unused_and_consumed_authority_without_waiting_for_poll(self):
        runtime, _ = await self.runtime()
        unused = runtime.acquire(("core.basic_rest_read",))
        used = runtime.acquire(("core.direct_entity_state_read",))
        commits = runtime.consume(used)
        notifications = []
        runtime.register_reconciliation_listener(lambda: notifications.append(True))
        self.clock += timedelta(days=2)
        self.assertIsNone(runtime.consume(unused))
        self.assertFalse(runtime.revalidate(used, commits))
        self.assertTrue(runtime.finish(commits))
        self.assertFalse(runtime.route_status(("core.basic_rest_read",))["available"])
        self.assertEqual(len(notifications), 1)

    async def _expire_at_authority_boundary(self, *, after_publication):
        # Desired-behavior regression from CORE-CONTINUITY-REVIEW-1. Cross
        # exact expiry by one microsecond without altering coordinator state.
        self.assertTrue(await self.registry.refresh())
        expiry = datetime.fromisoformat(
            json.loads(self.raw)["envelopes"][-1]["expires_at"].replace("Z", "+00:00")
        )
        self.clock = expiry - timedelta(microseconds=1)
        runtime = CoreRuntime()
        runtime.configure(object(), source=ProjectedCoreSource(self.registry),
                          release_registry=self.registry)
        owner = runtime._coordinator if after_publication else runtime
        method = "reconcile" if after_publication else "_authority_provider"
        original = getattr(owner, method)

        def expire(*args, **kwargs):
            result = original(*args, **kwargs)
            self.clock = expiry
            return result

        with patch.object(owner, method, side_effect=expire):
            await runtime.reconcile_once("startup")
        self.assertFalse(self.registry.authority().positive_authority_current)
        self.assertEqual(self.registry.selections("2026.9.2"), ())
        authority = runtime.acquire(("core.direct_entity_state_read",))
        commits = runtime.consume(authority) if authority else None
        try:
            self.assertIsNone(authority)
            self.assertIsNone(commits)
            self.assertFalse(runtime.route_status(("core.basic_rest_read",))["available"])
            self.assert_admitted(runtime, 0)
        finally:
            if commits:
                runtime.finish(commits)
            elif authority:
                runtime.release(authority)
        health = runtime.health_snapshot()
        self.assertEqual(health["issued_lease_count"], 0)
        self.assertEqual(health["active_commit_count"], 0)

    async def test_expiry_after_selection_cannot_publish_authority(self):
        await self._expire_at_authority_boundary(after_publication=False)

    async def test_expiry_during_publication_cannot_latch_expired_token(self):
        await self._expire_at_authority_boundary(after_publication=True)

    async def test_verified_registry_changes_at_selection_and_publication_retire_old_authority(self):
        for phase in ("selection", "publication"):
            for change in ("revocation", "replacement"):
                with self.subTest(phase=phase, change=change):
                    # Each case uses a separate real signature/cache boundary.
                    registry = CoreReleaseRegistry(
                        enabled=True, public_key=self.signer.public_key_base64,
                        cache_path=Path(self.tmp.name) / f"{phase}-{change}.json",
                        fetcher=self.registry._fetcher, now=lambda: self.clock,
                    )
                    self.raw = self.signer.journal_raw()
                    runtime = CoreRuntime()
                    runtime.configure(object(), source=ProjectedCoreSource(registry),
                                      release_registry=registry)
                    await runtime.reconcile_once("startup")
                    unused = runtime.acquire(("core.basic_rest_read",))
                    used = runtime.acquire(("core.direct_entity_state_read",))
                    commits = runtime.consume(used)
                    self.assertIsNotNone(commits)
                    entry = core_entry()
                    entry["capabilities"][0]["contract_fingerprint"] = "sha256:" + "f" * 64
                    self.raw = self.next_journal(
                        entries=[] if change == "revocation" else [entry],
                        revocations=[core_revocation()] if change == "revocation" else [],
                    )
                    owner = runtime if phase == "selection" else runtime._coordinator
                    method = "_authority_provider" if phase == "selection" else "reconcile"
                    original = getattr(owner, method)

                    def replace_registry(*args, **kwargs):
                        result = original(*args, **kwargs)
                        # Complete a genuine signed refresh between two synchronous
                        # publication steps, without forging accepted registry state.
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            self.assertTrue(pool.submit(
                                lambda: asyncio.run(registry.refresh())
                            ).result(timeout=3))
                        return result

                    try:
                        with patch.object(owner, method, side_effect=replace_registry):
                            await runtime.reconcile_once()
                        self.assertIsNone(runtime.consume(unused))
                        self.assertFalse(runtime.revalidate(used, commits))
                        self.assertIsNone(runtime.acquire(("core.basic_rest_read",)))
                        self.assert_admitted(runtime, 0)
                    finally:
                        runtime.release(unused)
                        self.assertTrue(runtime.finish(commits))
                    await runtime.reconcile_once()
                    self.assert_admitted(runtime, 0 if change == "revocation" else 16)
                    self.assertIsNone(runtime.acquire(("core.basic_rest_read",)))
                    if change == "replacement":
                        fresh = runtime.acquire(("core.direct_entity_state_read",))
                        fresh_commits = runtime.consume(fresh)
                        self.assertTrue(runtime.revalidate(fresh, fresh_commits))
                        self.assertTrue(runtime.finish(fresh_commits))
                    health = runtime.health_snapshot()
                    self.assertEqual(health["issued_lease_count"], 0)
                    self.assertEqual(health["active_commit_count"], 0)

    async def test_revocation_overrides_compiled_authority_and_survives_restart_expiry(self):
        runtime, _ = await self.runtime("2026.9.1")
        lease = runtime.acquire(("core.basic_rest_read",))
        self.raw = self.next_journal(revocations=[core_revocation("2026.9.1")])
        self.assertTrue(await self.registry.refresh())
        self.assertIsNone(runtime.consume(lease))
        self.clock += timedelta(days=2)
        restarted = CoreReleaseRegistry(enabled=True, public_key=self.signer.public_key_base64,
                                        cache_path=self.cache, now=lambda: self.clock)
        self.assertTrue(restarted.authority().revoked(CORE_IDENTITY, "2026.9.1"))
        self.assertTrue(all(s.status.value == "deny_only" for s in restarted.selections("2026.9.1")))
        self.assertEqual(restarted.selections("2026.9.2"), ())

    async def test_bad_contract_is_capability_local_and_unknown_addition_has_no_authority(self):
        entry = core_entry()
        entry["capabilities"][0]["contract_fingerprint"] = "sha256:" + "f" * 64
        unknown = deepcopy(entry["capabilities"][0])
        unknown["capability_id"] = "core.unreviewed_action"
        entry["capabilities"].append(unknown)
        self.raw = self.signer.journal_raw(entries=[entry])
        runtime, _ = await self.runtime()
        self.assert_admitted(runtime, 16)
        self.assertIsNone(runtime.acquire(("core.unreviewed_action",)))
        self.assertIsNone(runtime.acquire(("core.basic_rest_read",)))

    async def test_unknown_probe_profile_cannot_select_evidence(self):
        entry = core_entry()
        entry["probe_profile_id"] = "unreviewed-probes-v1"
        self.raw = self.signer.journal_raw(entries=[entry])
        runtime, _ = await self.runtime()
        self.assert_admitted(runtime, 0)

    async def test_malformed_device_registry_withholds_only_its_consumers(self):
        runtime, source = await self.runtime()
        source.fixture["devices"][0]["id"] = "x" * 129
        await runtime.reconcile_once()
        self.assert_admitted(runtime, 14)
        self.assertIsNone(runtime.acquire(("core.dependency_helper_planning",)))

    async def test_source_transition_retires_old_lease_and_restores_without_new_runtime(self):
        runtime, source = await self.runtime("2026.9.1")
        lease = runtime.acquire(("core.basic_rest_read",))
        source.version = "2026.9.2"
        await runtime.reconcile_once()
        self.assertIsNone(runtime.consume(lease))
        self.assert_admitted(runtime)

    async def test_observed_new_core_retires_old_authority_before_registry_network_wait(self):
        runtime, source = await self.runtime("2026.9.1")
        lease = runtime.acquire(("core.basic_rest_read",))
        entered, resume = asyncio.Event(), asyncio.Event()

        async def delayed_fetch(url, maximum):
            entered.set()
            await resume.wait()
            return self.raw

        self.registry._fetcher = delayed_fetch
        source.version = "2026.9.3"
        attempt = asyncio.create_task(runtime.reconcile_once())
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            self.assertIsNone(runtime.acquire(("core.basic_rest_read",)))
            self.assertIsNone(runtime.consume(lease))
        finally:
            resume.set()
            await attempt
        self.assert_admitted(runtime, 0)

    async def gateway(self, runtime):
        from mcp.server.fastmcp import FastMCP
        from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway
        from ha_mcp_engineering.upstream_tool_policy import load_reviewed_upstream_release_registry
        from tests.test_ha_core_2026_9_integration import FakeTransport, settings, _capture_for_release

        releases = load_reviewed_upstream_release_registry()
        release = releases.by_version["8.4.3"]
        capture = _capture_for_release(release)
        review = json.loads((ROOT / release.artifact_evidence_resource).read_text())
        descriptors = {item["name"]: item for item in capture["tools"]}
        transport = FakeTransport([descriptors[name] for name in review["runtime_catalog"]["runtime_tool_order"]], version="8.4.3")
        gateway = UpstreamReadGateway()
        gateway.configure(settings(), transport=transport, release_registry=releases, core_runtime=runtime)
        server = FastMCP("synthetic-core-continuity")
        await gateway.initialize(server)
        return gateway, server, transport

    async def test_signed_core_restores_real_gateway_catalog_and_successful_read(self):
        from ha_mcp_engineering.tools import registered_tools, ENGINEERING_STATIC_TOOL_COUNT
        runtime, source = await self.runtime()
        gateway, server, transport = await self.gateway(runtime)
        tools = registered_tools(server)
        self.assertEqual(len(tools), 25)
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT + len(tools), 76)
        self.assertNotIn("ha_get_operation_status", tools)
        result = json.loads(await tools["ha_get_state"].run({"entity_id": "sensor.synthetic"}))
        self.assertTrue(result["success"])
        self.assertEqual(len(transport.calls), 1)
        self.clock += timedelta(days=2)
        await gateway.initialize(server)
        self.assertEqual(len(registered_tools(server)), 0)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)

    async def test_publication_expiry_refuses_final_gateway_dispatch_and_cleans_authority(self):
        from ha_mcp_engineering.tools import registered_tools
        runtime, _ = await self.runtime()
        gateway, server, transport = await self.gateway(runtime)
        execute = transport.execute_read
        reconcile = runtime._coordinator.reconcile

        def expire(*args, **kwargs):
            result = reconcile(*args, **kwargs)
            self.clock += timedelta(days=2)
            return result

        async def cross_publication(*args, **kwargs):
            with patch.object(runtime._coordinator, "reconcile", side_effect=expire):
                await runtime.reconcile_once()
            return await execute(*args, **kwargs)

        with patch.object(transport, "execute_read", side_effect=cross_publication):
            result = json.loads(await registered_tools(server)["ha_get_state"].run(
                {"entity_id": "sensor.synthetic"}
            ))
        self.assertFalse(result["success"])
        self.assertEqual(len(transport.attempts), 1)
        self.assertEqual(transport.calls, [])
        self.assert_admitted(runtime, 0)
        health = runtime.health_snapshot()
        for name in ("issued_lease_count", "active_commit_count", "fallback_count"):
            self.assertEqual(health[name], 0)
        self.assertEqual(gateway.health_snapshot()["fallback_count"], 0)

    async def test_cache_persistence_failure_cannot_revive_revoked_authority(self):
        runtime, _ = await self.runtime()
        self.raw = self.next_journal(entries=[], revocations=[core_revocation()])
        with patch.object(self.registry, "_write_cache", side_effect=ReleaseRegistryOperationalError("registry_cache_write_failed")):
            self.assertFalse(await self.registry.refresh())
        self.assertIsNone(runtime.acquire(("core.basic_rest_read",)))
        restarted = CoreReleaseRegistry(enabled=True, public_key=self.signer.public_key_base64,
                                        cache_path=self.cache, now=lambda: self.clock)
        self.assertTrue(all(s.status.value == "deny_only" for s in restarted.selections("2026.9.2")))

    async def test_rollback_replay_and_unsigned_content_do_not_replace_current_entry(self):
        runtime, _ = await self.runtime()
        first = self.raw
        self.raw = self.next_journal()
        self.assertTrue(await self.registry.refresh())
        self.raw = first
        self.assertFalse(await self.registry.refresh())
        self.assertEqual(self.registry.authority().sequence, 2)
        value = json.loads(first)
        value["envelopes"][0]["entries"][0]["version"] = "2026.9.3"
        self.raw = canonical_json(value)
        self.assertFalse(await self.registry.refresh())
        self.assertEqual(self.registry.selections("2026.9.3"), ())
        self.assert_admitted(runtime)

    async def test_core_cache_and_journal_cannot_supply_ha_mcp_authority(self):
        await self.runtime()
        other = SignedReleaseRegistry(enabled=True, public_key=self.signer.public_key_base64,
                                      cache_path=self.cache, fetcher=self.registry._fetcher,
                                      now=lambda: self.clock)
        self.assertTrue(other.authority().surface_denied)
        self.assertFalse(other.authority().positive_authority_current)

    async def test_authority_change_during_collection_never_publishes_mixed_evidence(self):
        runtime, source = await self.runtime()
        original = source.capture_core_snapshot
        entered, resume = asyncio.Event(), asyncio.Event()

        async def delayed():
            value = await original()
            if not entered.is_set():
                entered.set()
                await resume.wait()
            return value

        source.capture_core_snapshot = delayed
        attempt = asyncio.create_task(runtime.reconcile_once())
        await entered.wait()
        self.raw = self.next_journal(entries=[], revocations=[core_revocation()])
        self.assertTrue(await self.registry.refresh())
        resume.set()
        await attempt
        self.assertFalse(runtime.route_status(("core.basic_rest_read",))["available"])
        self.assertFalse(runtime.initialized)

    def test_entry_rejects_extra_authority_fields_and_duplicate_capabilities(self):
        for change in (lambda e: e.update(provider_url="https://synthetic.invalid"),
                       lambda e: e["capabilities"].append(deepcopy(e["capabilities"][0])),
                       lambda e: e.update(source_commit="not-a-source-sha")):
            entry = core_entry()
            change(entry)
            with self.assertRaises(RegistryValidationError):
                CoreRegistryEnvelope.from_bytes(self.signer.raw(entries=[entry]))


if __name__ == "__main__":
    unittest.main()
