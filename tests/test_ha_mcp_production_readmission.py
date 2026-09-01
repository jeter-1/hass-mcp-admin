from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
TESTS = ROOT / "tests"
FOUNDATION = (
    TESTS / "fixtures" / "automatic_readmission" / "foundation_v1.json"
)
VECTORS = (
    TESTS
    / "fixtures"
    / "automatic_readmission"
    / "contract_vectors_v2.json"
)
sys.path.insert(0, str(BETA))
sys.path.insert(0, str(TESTS))

from ha_mcp_engineering.ha_mcp_readmission import (  # noqa: E402
    AdmissionDisposition,
    AuthorityBundle,
    AuthorityDecision,
    AuthoritySource,
    AuthorityStatus,
    CapabilityAdmissionCoordinator,
    CapabilityContract,
    CapabilityKind,
    CapabilityProfile,
    CompatibilityModelError,
    CompatibilityObservation,
    ObservedCapability,
    UpstreamSurface,
    classify_registry_refresh,
)
from ha_mcp_engineering.ha_mcp_readmission.registry import (  # noqa: E402
    REGISTRY_URL,
    SignedReleaseRegistry,
    TRUST_ANCHOR_KEY_ID,
)
from ha_mcp_engineering.clients.upstream_read import (  # noqa: E402
    McpReadCatalog,
    McpReadResult,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.application import validate_settings  # noqa: E402
from ha_mcp_engineering.errors import ConfigurationError  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    UpstreamReadGateway,
)
from ha_mcp_engineering.signed_registry import (  # noqa: E402
    RegistryEnvelope,
)
from ha_mcp_engineering.tools import (  # noqa: E402
    ENGINEERING_STATIC_TOOL_COUNT,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    load_reviewed_upstream_release_registry,
    schema_fingerprint,
)
from signed_registry_fixtures import (  # noqa: E402
    NOW,
    RegistrySigner,
    revocation,
)
from support.automatic_readmission import (  # noqa: E402
    OfflineUpdateHarness as ReferenceHarness,
    ReferenceContractAdapter,
    run_contract_suite,
)


class _ProductionHarness:
    """Translate data-only fixtures into the production-owned contract types."""

    def __init__(self, reference: ReferenceHarness):
        self._reference = reference
        self.profiles = tuple(
            _production_profile(item) for item in reference.profiles
        )

    def observation(self, scenario_id: str) -> CompatibilityObservation:
        item = self._reference.observation(scenario_id)
        return CompatibilityObservation(
            surface=UpstreamSurface(item.surface.value),
            identity=item.identity,
            version=item.version,
            protocol_version=item.protocol_version,
            session_id=item.session_id,
            capabilities=tuple(
                ObservedCapability(
                    capability_id=value.capability_id,
                    kind=CapabilityKind(value.kind.value),
                    contract_fingerprint=value.contract_fingerprint,
                )
                for value in item.capabilities
            ),
            connected=item.connected,
            authenticated=item.authenticated,
            catalog_complete=item.catalog_complete,
            evidence_reason=item.evidence_reason,
            core_rest_version=item.core_rest_version,
            core_websocket_auth_version=(
                item.core_websocket_auth_version
            ),
            core_websocket_config_version=(
                item.core_websocket_config_version
            ),
        )

    def authority(self, authority_id: str) -> AuthorityBundle:
        item = self._reference.authority(authority_id)
        return AuthorityBundle(
            evaluated_at_epoch=item.evaluated_at_epoch,
            decisions=tuple(
                _production_authority(value) for value in item.decisions
            ),
        )


def _production_profile(item) -> CapabilityProfile:
    return CapabilityProfile(
        profile_id=item.profile_id,
        profile_version=item.profile_version,
        surface=UpstreamSurface(item.surface.value),
        adapter_id=item.adapter_id,
        expected_identity=item.expected_identity,
        supported_protocols=item.supported_protocols,
        capabilities=tuple(
            CapabilityContract(
                capability_id=value.capability_id,
                kind=CapabilityKind(value.kind.value),
                contract_fingerprint=value.contract_fingerprint,
            )
            for value in item.capabilities
        ),
    )


def _production_authority(item) -> AuthorityDecision:
    return AuthorityDecision(
        source=AuthoritySource(item.source.value),
        status=AuthorityStatus(item.status.value),
        profile_id=item.profile_id,
        profile_version=item.profile_version,
        adapter_id=item.adapter_id,
        subject_identity=item.subject_identity,
        subject_version=item.subject_version,
        protocol_version=item.protocol_version,
        capability_ids=item.capability_ids,
        reason_code=item.reason_code,
        registry_sequence=item.registry_sequence,
        registry_digest=item.registry_digest,
        expires_at_epoch=item.expires_at_epoch,
    )


class ProductionContractAdapter(ReferenceContractAdapter):
    """Drive the production coordinator through the neutral vector API."""

    def __init__(self, foundation):
        self._foundation = deepcopy(dict(foundation))
        harness = _ProductionHarness(
            ReferenceHarness.from_mapping(self._foundation)
        )
        self._coordinator = CapabilityAdmissionCoordinator(
            harness.profiles
        )
        self._attempts = {}
        self._leases = {}
        self._commits = {}
        self._last_reconciliation = None

    def _harness(self, arguments):
        return _ProductionHarness(super()._harness(arguments))

    def _reconcile(self, arguments):
        harness = self._harness(arguments)
        result = self._coordinator.reconcile(
            harness.observation(arguments["observation_id"]),
            self._authority(harness, arguments),
        )
        self._last_reconciliation = result
        return _normalized_production_reconciliation(result)

    def _complete(self, arguments):
        attempt = self._attempts.get(arguments["attempt_id"])
        if attempt is None:
            raise CompatibilityModelError("vector_attempt_unknown")
        result = self._coordinator.complete_reconciliation(attempt)
        self._last_reconciliation = result
        return _normalized_production_reconciliation(result)

    @staticmethod
    def _authority(harness, arguments):
        authority_ids = arguments.get("authority_ids")
        if authority_ids is None:
            return harness.authority(arguments["authority_id"])
        bundles = tuple(
            harness.authority(identifier) for identifier in authority_ids
        )
        return AuthorityBundle(
            evaluated_at_epoch=max(
                item.evaluated_at_epoch for item in bundles
            ),
            decisions=tuple(
                decision
                for bundle in bundles
                for decision in bundle.decisions
            ),
        )

    @staticmethod
    def _registry_refresh(arguments):
        result = classify_registry_refresh(**dict(arguments))
        return {
            "status": result.status.value,
            "accepted": result.accepted,
            "idempotent": result.idempotent,
        }

    def _validate_fixture(self, arguments):
        try:
            harness = self._harness(arguments)
            access = arguments.get("access")
            if not isinstance(access, dict):
                raise CompatibilityModelError("vector_access_invalid")
            if access.get("kind") == "observation":
                harness.observation(access.get("id"))
            elif access.get("kind") == "authority":
                harness.authority(access.get("id"))
            elif access.get("kind") == "coordinator":
                CapabilityAdmissionCoordinator(harness.profiles)
            else:
                raise CompatibilityModelError("vector_access_invalid")
        except Exception as exc:
            code = getattr(exc, "code", "vector_fixture_invalid")
            return {"accepted": False, "error_code": code}
        return {"accepted": True, "error_code": None}


def _normalized_production_reconciliation(result):
    generation = result.generation
    decisions = generation.decisions if generation is not None else ()
    return {
        "published": result.published,
        "idempotent": result.idempotent,
        "surface": generation.surface.value if generation else None,
        "generation": generation.generation if generation else None,
        "retired_generation": result.retired_generation,
        "disposition": result.disposition.value,
        "admitted": sorted(
            item.capability_id
            for item in decisions
            if item.disposition.admitted
        ),
        "quarantined": sorted(
            [
                {
                    "capability_id": item.capability_id,
                    "reason_code": item.reason_code,
                }
                for item in decisions
                if item.disposition
                is AdmissionDisposition.QUARANTINED
            ],
            key=lambda item: (item["capability_id"], item["reason_code"]),
        ),
        "unavailable": sorted(
            [
                {
                    "capability_id": item.capability_id,
                    "reason_code": item.reason_code,
                }
                for item in decisions
                if item.disposition
                is AdmissionDisposition.UNAVAILABLE
            ],
            key=lambda item: (item["capability_id"], item["reason_code"]),
        ),
        "write_action_reachability": 0,
    }


class ProductionVectorTests(unittest.TestCase):
    def test_every_adr020_vector_matches_the_production_coordinator(self):
        foundation = json.loads(FOUNDATION.read_text(encoding="utf-8"))
        vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
        report = run_contract_suite(
            vectors,
            lambda: ProductionContractAdapter(foundation),
        )
        self.assertTrue(report["matched"], report["reports"])
        self.assertEqual(report["vector_count"], 20)
        self.assertEqual(report["step_count"], 136)
        self.assertEqual(report["mismatch_count"], 0)

    def test_production_and_reference_vector_reports_are_identical(self):
        foundation = json.loads(FOUNDATION.read_text(encoding="utf-8"))
        vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
        production = run_contract_suite(
            vectors,
            lambda: ProductionContractAdapter(foundation),
        )
        reference = run_contract_suite(
            vectors,
            lambda: ReferenceContractAdapter(foundation),
        )
        self.assertEqual(production, reference)

    def test_production_runtime_never_imports_test_support(self):
        runtime = BETA / "ha_mcp_engineering"
        for path in runtime.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("support.automatic_readmission", text, path)
            self.assertNotIn("tests.support", text, path)

    def test_sequential_and_concurrent_duplicate_commit_are_rejected(self):
        foundation = json.loads(FOUNDATION.read_text(encoding="utf-8"))
        harness = _ProductionHarness(
            ReferenceHarness.from_mapping(foundation)
        )
        coordinator = CapabilityAdmissionCoordinator(harness.profiles)
        coordinator.reconcile(
            harness.observation("ha_mcp_exact"),
            harness.authority("compiled_ha_mcp_exact"),
        )
        sequential = coordinator.acquire_route(
            "ha_get_state",
            session_id="synthetic-session-ha-mcp-1",
        )
        self.assertIsNotNone(sequential)
        first = coordinator.commit_route(
            sequential,
            session_id="synthetic-session-ha-mcp-1",
        )
        second = coordinator.commit_route(
            sequential,
            session_id="synthetic-session-ha-mcp-1",
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)

        concurrent = coordinator.acquire_route(
            "ha_search",
            session_id="synthetic-session-ha-mcp-1",
        )
        self.assertIsNotNone(concurrent)
        barrier = Barrier(2)

        def commit_once():
            barrier.wait()
            return coordinator.commit_route(
                concurrent,
                session_id="synthetic-session-ha-mcp-1",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                item.result(timeout=5)
                for item in (
                    executor.submit(commit_once),
                    executor.submit(commit_once),
                )
            )
        self.assertEqual(sum(item is not None for item in results), 1)


class OperationalSignedRegistryTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_cache_retains_signed_revocation_as_denial_only(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        first = signer.raw(
            sequence=1,
            entries=[],
            revocations=[revocation(version="7.14.2")],
        )
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            entries=[],
            revocations=[],
            generated_at=NOW,
            expires_at=NOW + timedelta(days=1),
        )
        responses = [first, second]

        async def fetcher(url, maximum):
            self.assertEqual(url, REGISTRY_URL)
            self.assertGreater(maximum, 0)
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "registry-cache.json"
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await registry.refresh())
            self.assertTrue(await registry.refresh())
            authority = registry.authority()
            self.assertTrue(authority.positive_authority_current)
            self.assertTrue(authority.revoked("ha-mcp", "7.14.2"))

            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW + timedelta(days=2),
            )
            restored = restarted.authority()
            self.assertFalse(restored.positive_authority_current)
            self.assertTrue(restored.revoked("ha-mcp", "7.14.2"))
            self.assertEqual(
                restarted.snapshot()["freshness_status"],
                "denial_only",
            )

    async def test_rollback_replay_conflict_and_malformed_fail_closed(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        first = signer.raw(sequence=1, entries=[], revocations=[])
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            entries=[],
            revocations=[],
            generated_at=NOW,
        )
        conflict = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            entries=[],
            revocations=[revocation()],
            generated_at=NOW,
        )
        responses = [first, second, first, conflict, b"not-json"]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=Path(directory) / "cache.json",
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await registry.refresh())
            self.assertTrue(await registry.refresh())
            accepted = registry.authority()
            self.assertEqual(accepted.sequence, 2)
            for expected_reason in (
                "registry_sequence_rollback",
                "registry_sequence_replay_conflict",
                "registry_malformed_json",
            ):
                self.assertFalse(await registry.refresh())
                self.assertEqual(registry.authority(), accepted)
                self.assertEqual(
                    registry.snapshot()["last_failure_reason"],
                    expected_reason,
                )

    async def test_expired_unknown_key_and_tampering_never_enter_cache(self):
        trusted = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        unknown = RegistrySigner(key_id="synthetic-unknown-key")
        expired = trusted.raw(
            sequence=1,
            entries=[],
            revocations=[],
            generated_at=NOW - timedelta(days=2),
            expires_at=NOW - timedelta(days=1),
        )
        unknown_key = unknown.raw(
            sequence=1,
            entries=[],
            revocations=[],
        )
        tampered_mapping = json.loads(
            trusted.raw(
                sequence=1,
                entries=[],
                revocations=[],
            ).decode("utf-8")
        )
        tampered_mapping["registry_id"] = "ha-mcp-other-registry"
        tampered = json.dumps(tampered_mapping).encode("utf-8")
        responses = [expired, unknown_key, tampered]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=trusted.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            for expected in (
                "registry_expired",
                "registry_unknown_key",
                "registry_invalid_signature",
            ):
                self.assertFalse(await registry.refresh())
                self.assertIsNone(registry.authority().envelope)
                self.assertEqual(
                    registry.snapshot()["last_failure_reason"],
                    expected,
                )
            self.assertFalse(cache.exists())

    async def test_corrupt_cache_fails_closed_without_partial_authority(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            cache.write_bytes(b'{"schema_version":1,"accepted_registry":')
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                now=lambda: NOW,
            )
            self.assertIsNone(registry.authority().envelope)
            self.assertEqual(registry.snapshot()["cache_status"], "invalid")

    async def test_persistence_failure_never_publishes_candidate(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)

        async def fetcher(_url, _maximum):
            return signer.raw(sequence=1, entries=[], revocations=[])

        with tempfile.TemporaryDirectory() as directory:
            blocked_parent = Path(directory) / "not-a-directory"
            blocked_parent.write_text("blocked", encoding="utf-8")
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=blocked_parent / "cache.json",
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertFalse(await registry.refresh())
            self.assertIsNone(registry.authority().envelope)
            self.assertEqual(
                registry.snapshot()["last_failure_reason"],
                "registry_cache_write_failed",
            )

    async def test_directory_fsync_failure_never_publishes_candidate(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)

        async def fetcher(_url, _maximum):
            return signer.raw(sequence=1, entries=[], revocations=[])

        real_fsync = os.fsync

        def fail_directory_fsync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("synthetic directory fsync failure")
            return real_fsync(descriptor)

        with tempfile.TemporaryDirectory() as directory:
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=Path(directory) / "cache.json",
                fetcher=fetcher,
                now=lambda: NOW,
            )
            with patch(
                "ha_mcp_engineering.ha_mcp_readmission.registry.os.fsync",
                side_effect=fail_directory_fsync,
            ):
                self.assertFalse(await registry.refresh())
            self.assertIsNone(registry.authority().envelope)
            self.assertFalse(registry._cache_path.exists())
            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=registry._cache_path,
                now=lambda: NOW,
            )
            self.assertIsNone(restarted.authority().envelope)
            self.assertEqual(
                registry.snapshot()["last_failure_reason"],
                "registry_cache_write_failed",
            )

    async def test_failed_replacement_preserves_previous_cache(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        first = signer.raw(sequence=1, entries=[], revocations=[])
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            entries=[],
            revocations=[],
            generated_at=NOW,
        )
        responses = [first, second]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        real_fsync = os.fsync
        directory_calls = 0

        def fail_final_directory_fsync(descriptor):
            nonlocal directory_calls
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                directory_calls += 1
                if directory_calls == 2:
                    raise OSError("synthetic replacement fsync failure")
            return real_fsync(descriptor)

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await registry.refresh())
            with patch(
                "ha_mcp_engineering.ha_mcp_readmission.registry.os.fsync",
                side_effect=fail_final_directory_fsync,
            ):
                self.assertFalse(await registry.refresh())
            self.assertEqual(registry.authority().sequence, 1)

            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                now=lambda: NOW,
            )
            self.assertEqual(restarted.authority().sequence, 1)

    def test_distinct_trust_configuration_fails_closed(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        valid = replace(
            _settings(signer.public_key_base64),
            access_secret="s" * 32,
        )
        validate_settings(valid)
        with self.assertRaises(ConfigurationError):
            validate_settings(
                replace(
                    valid,
                    ha_mcp_release_registry_public_key="invalid",
                )
            )


def _settings(public_key: str) -> Settings:
    return Settings(
        ha_url="http://supervisor/core",
        ha_token="synthetic-ha-token",
        access_secret="synthetic-engineering-secret",
        port=8100,
        audit_path="synthetic-audit.jsonl",
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        upstream_dashboard_mcp_url=(
            "http://upstream:9583/synthetic-upstream-secret/mcp"
        ),
        ha_mcp_release_registry_enabled=True,
        ha_mcp_release_registry_public_key=public_key,
    )


def _capture_for_release(release) -> dict:
    return json.loads(
        (ROOT / release.capture_resource).read_text(encoding="utf-8")
    )


def _signed_entry_for(
    release,
    *,
    version: str,
    changed_tool: str | None = None,
) -> dict:
    policy = release.policy.by_name
    contracts = []
    for name, contract in sorted(release.tool_contracts):
        input_fingerprint = contract.input_schema_fingerprint
        if name == changed_tool:
            input_fingerprint = (
                "0" * 64
                if input_fingerprint != "0" * 64
                else "1" * 64
            )
        contracts.append(
            {
                "tool_name": name,
                "input_schema_fingerprint": input_fingerprint,
                "description_fingerprint": (
                    contract.description_fingerprint
                ),
                "annotation_fingerprint": (
                    contract.annotation_fingerprint
                ),
                "output_contract_fingerprint": (
                    contract.output_contract_fingerprint
                ),
                "runtime_contract_fingerprint": (
                    contract.runtime_contract_fingerprint
                ),
                "policy_classification": (
                    contract.policy_classification
                ),
                "reviewed_automatic_read": (
                    contract.reviewed_automatic_read
                ),
                "quarantine_reason": contract.quarantine_reason,
                "argument_restrictions": list(
                    policy[name].argument_restrictions
                ),
            }
        )
    first_name = contracts[0]["tool_name"]
    return {
        "entry_id": f"ha-mcp-v{version}-synthetic-signed",
        "approval_status": "reviewed",
        "server_name": release.server_name,
        "version": version,
        "allowed_protocol_versions": list(
            release.allowed_protocol_versions
        ),
        "source_repository": release.source_repository,
        "release_tag": f"v{version}",
        "source_commit": release.source_commit,
        "image_index_digest": release.image_index_digest,
        "architecture_image_digests": dict(
            release.architecture_image_digests
        ),
        "image_revision": release.image_revision,
        "advertised_tool_count": release.advertised_tool_count,
        "catalog_fingerprint": release.catalog_fingerprint,
        "capture_resource": release.capture_resource,
        "capture_sha256": release.capture_sha256,
        "capture_format_version": release.capture_format_version,
        "policy_resource": release.policy_resource,
        "policy_sha256": release.policy_sha256,
        "review_provenance": list(release.review_provenance),
        "review_date": release.review_date,
        "dashboard_attestation": {
            "status": release.dashboard_attestation_status,
            "entry_id": release.dashboard_attestation_entry_id,
            "attestation_fingerprint": (
                release.dashboard_attestation_fingerprint
            ),
            "compiled_constraints_fingerprint": (
                release.dashboard_compiled_constraints_fingerprint
            ),
        },
        "error_contract_fingerprint": (
            release.error_contract_fingerprint
        ),
        "entity_lookup_missing_resource_status": (
            release.entity_lookup_missing_resource_status
        ),
        "tool_contracts": contracts,
        "provider_argument_constraints": [
            {
                "provider_id": "upstream_read_gateway",
                "tool_name": first_name,
                "constraints_fingerprint": schema_fingerprint(
                    {
                        "argument_restrictions": list(
                            policy[first_name].argument_restrictions
                        )
                    }
                ),
            }
        ],
    }


class _GatewayTransport:
    def __init__(
        self,
        tools,
        *,
        version: str,
        catalog_complete: bool = True,
        session_id: str = "synthetic-ha-mcp-session",
        server_name: str = "ha-mcp",
        protocol_version: str = "2025-03-26",
    ) -> None:
        self.catalog = McpReadCatalog(
            protocol_version=protocol_version,
            server_name=server_name,
            server_version=version,
            tools=tuple(deepcopy(tools)),
            connection_latency_ms=1.0,
            session_id=session_id,
            catalog_complete=catalog_complete,
        )
        self.calls = 0

    async def discover(self):
        return self.catalog

    async def execute_read(
        self,
        tool_name,
        arguments,
        *,
        timeout_seconds,
        catalog_validator,
        before_dispatch=None,
    ):
        catalog_validator(self.catalog)
        if before_dispatch is not None:
            result = before_dispatch()
            if inspect.isawaitable(result):
                await result
        self.calls += 1
        return McpReadResult(
            protocol_version=self.catalog.protocol_version,
            server_name=self.catalog.server_name,
            server_version=self.catalog.server_version,
            call_result={
                "content": [{"type": "text", "text": "{}"}],
                "isError": False,
            },
            connection_latency_ms=1.0,
            tool_call_latency_ms=1.0,
        )


class SignedGatewayReadmissionTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.compiled = load_reviewed_upstream_release_registry()
        self.release = self.compiled.by_version["8.2.0"]
        self.capture = _capture_for_release(self.release)
        self.signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        self.now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def _raw(
        self,
        *,
        entry: dict | None,
        revocations: list[dict] | None = None,
    ) -> bytes:
        return self.signer.raw(
            sequence=1,
            generated_at=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(days=1),
            entries=[] if entry is None else [entry],
            revocations=revocations or [],
        )

    def _registry(self, raw: bytes) -> SignedReleaseRegistry:
        async def fetcher(url, maximum):
            self.assertEqual(url, REGISTRY_URL)
            self.assertLessEqual(len(raw), maximum)
            return raw

        return SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=(
                Path(self.temporary.name) / "signed-registry-cache.json"
            ),
            fetcher=fetcher,
            now=lambda: self.now,
        )

    async def _initialize(
        self,
        *,
        raw: bytes,
        version: str,
        tools=None,
        catalog_complete: bool = True,
    ):
        transport = _GatewayTransport(
            tools if tools is not None else self.capture["tools"],
            version=version,
            catalog_complete=catalog_complete,
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=self._registry(raw),
        )
        server = FastMCP("signed-readmission-test")
        snapshot = await gateway.initialize(server)
        return gateway, transport, snapshot

    async def test_compatible_signed_update_restores_reads_without_restart(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        registry = self._registry(self._raw(entry=entry))
        transport = _GatewayTransport(
            self.capture["tools"],
            version="8.2.0",
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=registry,
        )
        server = FastMCP("signed-readmission-no-restart")
        first = await gateway.initialize(server)
        self.assertEqual(first["dynamically_exposed_count"], 25)
        self.assertEqual(
            first["readmission_authority_source"],
            "compiled_exact",
        )

        transport.catalog = McpReadCatalog(
            protocol_version="2025-03-26",
            server_name="ha-mcp",
            server_version=version,
            tools=tuple(deepcopy(self.capture["tools"])),
            connection_latency_ms=1.0,
            session_id="synthetic-ha-mcp-session",
            catalog_complete=True,
        )
        second = await gateway.initialize(server)
        self.assertEqual(second["dynamically_exposed_count"], 25)
        self.assertEqual(
            second["readmission_authority_source"],
            "signed_registry",
        )
        self.assertEqual(second["fallback_count"], 0)
        self.assertEqual(
            second["selected_compatibility_entry_id"],
            entry["entry_id"],
        )
        generation = second["readmission_generation"]
        self.assertEqual(
            {
                item.generation
                for item in gateway._exposed.values()
            },
            {generation},
        )
        self.assertEqual(
            {
                item["admission_generation"]
                for item in gateway._dynamic_capabilities
            },
            {generation},
        )
        registered = gateway._registered_tool_registry.snapshot()
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT, 51)
        self.assertEqual(len(registered), 25)
        self.assertEqual(ENGINEERING_STATIC_TOOL_COUNT + len(registered), 76)
        self.assertNotIn("ha_get_operation_status", registered)
        for policy_entry in self.release.policy.tools:
            if policy_entry.classification != "automatic_read":
                self.assertNotIn(policy_entry.exposed_name, registered)
        self.assertEqual(gateway._held_canaries, {})

    async def test_disabled_registry_preserves_beta54_compiled_exact_behavior(self):
        transport = _GatewayTransport(
            self.capture["tools"],
            version="8.2.0",
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            replace(
                _settings(self.signer.public_key_base64),
                ha_mcp_release_registry_enabled=False,
                ha_mcp_release_registry_public_key="",
            ),
            transport=transport,
            release_registry=self.compiled,
        )
        server = FastMCP("compiled-exact-feature-disabled")
        snapshot = await gateway.initialize(server)
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        self.assertEqual(snapshot["held_tools"], ["ha_get_operation_status"])
        self.assertNotIn(
            "ha_get_operation_status",
            gateway._registered_tool_registry.snapshot(),
        )
        self.assertNotIn("automatic_readmission", snapshot)
        self.assertNotIn("automatic_readmission_registry", snapshot)
        self.assertNotIn("readmission_authority_source", snapshot)
        self.assertIsNone(gateway._readmission_selector)
        self.assertIsNone(gateway._readmission_coordinator)
        self.assertEqual(gateway.readmission_audit_snapshot(), ())
        self.assertEqual(transport.calls, 0)

    async def test_changed_signed_read_is_quarantined_but_siblings_return(self):
        version = "8.2.1"
        automatic = next(
            item.upstream_name
            for item in self.release.policy.tools
            if item.classification == "automatic_read"
        )
        entry = _signed_entry_for(
            self.release,
            version=version,
            changed_tool=automatic,
        )
        _gateway, _transport, snapshot = await self._initialize(
            raw=self._raw(entry=entry),
            version=version,
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 24)
        self.assertEqual(snapshot["quarantined_automatic_read_count"], 1)
        self.assertNotIn(automatic, snapshot["exposed_tools"])
        self.assertEqual(snapshot["fallback_count"], 0)

    async def test_each_signed_contract_component_is_independently_required(self):
        version = "8.2.1"
        automatic = next(
            item.upstream_name
            for item in self.release.policy.tools
            if item.classification == "automatic_read"
        )
        fields = (
            "input_schema_fingerprint",
            "description_fingerprint",
            "annotation_fingerprint",
            "output_contract_fingerprint",
            "runtime_contract_fingerprint",
        )
        for field in fields:
            with self.subTest(field=field):
                entry = _signed_entry_for(self.release, version=version)
                contract = next(
                    item
                    for item in entry["tool_contracts"]
                    if item["tool_name"] == automatic
                )
                contract[field] = (
                    "0" * 64
                    if contract[field] != "0" * 64
                    else "1" * 64
                )
                _gateway, transport, snapshot = await self._initialize(
                    raw=self._raw(entry=entry),
                    version=version,
                )
                self.assertEqual(snapshot["dynamically_exposed_count"], 24)
                self.assertNotIn(automatic, snapshot["exposed_tools"])
                self.assertEqual(transport.calls, 0)

    async def test_changed_argument_restriction_withholds_only_that_read(self):
        version = "8.2.1"
        automatic = next(
            item.upstream_name
            for item in self.release.policy.tools
            if item.classification == "automatic_read"
        )
        entry = _signed_entry_for(self.release, version=version)
        contract = next(
            item
            for item in entry["tool_contracts"]
            if item["tool_name"] == automatic
        )
        contract["argument_restrictions"] = [
            "synthetic_constraint_not_compiled"
        ]
        _gateway, transport, snapshot = await self._initialize(
            raw=self._raw(entry=entry),
            version=version,
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 24)
        self.assertNotIn(automatic, snapshot["exposed_tools"])
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_unknown_addition_never_becomes_callable(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        tools = deepcopy(self.capture["tools"])
        tools.append(
            {
                "name": "synthetic_future_write",
                "description": "Synthetic unreviewed write.",
                "inputSchema": {"type": "object"},
            }
        )
        _gateway, transport, snapshot = await self._initialize(
            raw=self._raw(entry=entry),
            version=version,
            tools=tools,
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        self.assertNotIn("synthetic_future_write", snapshot["exposed_tools"])
        self.assertEqual(snapshot["unreviewed_tool_count"], 1)
        self.assertEqual(transport.calls, 0)

    async def test_incomplete_duplicate_and_malformed_catalogs_fail_closed(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        raw = self._raw(entry=entry)
        selected_name = next(
            item.upstream_name
            for item in self.release.policy.tools
            if item.classification == "automatic_read"
        )
        selected = next(
            item
            for item in self.capture["tools"]
            if item["name"] == selected_name
        )
        cases = {
            "incomplete": (
                self.capture["tools"],
                False,
                0,
            ),
            "duplicate": (
                self.capture["tools"] + [deepcopy(selected)],
                True,
                24,
            ),
            "malformed": (
                [
                    {
                        **deepcopy(item),
                        **(
                            {"inputSchema": "invalid"}
                            if item["name"] == selected_name
                            else {}
                        ),
                    }
                    for item in self.capture["tools"]
                ],
                True,
                24,
            ),
        }
        for name, (tools, complete, expected) in cases.items():
            with self.subTest(name=name):
                _gateway, transport, snapshot = await self._initialize(
                    raw=raw,
                    version=version,
                    tools=tools,
                    catalog_complete=complete,
                )
                self.assertEqual(
                    snapshot["dynamically_exposed_count"],
                    expected,
                )
                self.assertEqual(transport.calls, 0)

    async def test_signed_revocation_retires_existing_routes(self):
        signed_revocation = {
            "entry_id": self.release.entry_id,
            "server_name": "ha-mcp",
            "version": "8.2.0",
            "image_index_digest": self.release.image_index_digest,
            "revoked_at": (
                self.now - timedelta(minutes=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": "Synthetic exact release revocation.",
        }
        _gateway, transport, snapshot = await self._initialize(
            raw=self._raw(
                entry=None,
                revocations=[signed_revocation],
            ),
            version="8.2.0",
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 0)
        self.assertEqual(
            snapshot["automatic_readmission"]["surface"]["disposition"],
            "quarantined",
        )
        self.assertEqual(transport.calls, 0)

    async def test_transport_restoration_has_no_unsigned_authority(self):
        _gateway, transport, snapshot = await self._initialize(
            raw=self._raw(entry=None),
            version="8.2.1",
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 0)
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_unexpired_signed_cache_survives_restart_fetch_failure(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        raw = self._raw(entry=entry)
        first_registry = self._registry(raw)
        self.assertTrue(await first_registry.refresh())

        async def unavailable_fetcher(_url, _maximum):
            raise OSError("synthetic registry outage")

        restarted_registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=first_registry._cache_path,
            fetcher=unavailable_fetcher,
            now=lambda: self.now,
        )
        transport = _GatewayTransport(
            self.capture["tools"],
            version=version,
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=restarted_registry,
        )
        snapshot = await gateway.initialize(
            FastMCP("signed-readmission-restarted-cache")
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        self.assertEqual(
            snapshot["readmission_authority_source"],
            "signed_registry",
        )
        self.assertEqual(
            snapshot["automatic_readmission_registry"]["refresh_status"],
            "failed",
        )
        self.assertEqual(snapshot["fallback_count"], 0)
        self.assertEqual(transport.calls, 0)

    async def test_signed_entry_cannot_select_unknown_profile_or_protocol(self):
        version = "8.2.1"
        wrong_profile = _signed_entry_for(
            self.release,
            version=version,
        )
        wrong_profile["policy_sha256"] = "sha256:" + "0" * 64
        wrong_protocol = _signed_entry_for(
            self.release,
            version=version,
        )
        wrong_protocol["allowed_protocol_versions"] = ["2025-06-18"]
        for name, entry in (
            ("unknown_profile", wrong_profile),
            ("unapproved_protocol", wrong_protocol),
        ):
            with self.subTest(name=name):
                _gateway, transport, snapshot = await self._initialize(
                    raw=self._raw(entry=entry),
                    version=version,
                )
                self.assertEqual(
                    snapshot["dynamically_exposed_count"],
                    0,
                )
                self.assertEqual(snapshot["fallback_count"], 0)
                self.assertEqual(transport.calls, 0)

    async def test_observed_identity_and_protocol_mismatch_fail_closed(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        raw = self._raw(entry=entry)
        cases = (
            ("other-mcp", "2025-03-26"),
            ("ha-mcp", "2025-06-18"),
        )
        for server_name, protocol_version in cases:
            with self.subTest(
                server_name=server_name,
                protocol_version=protocol_version,
            ):
                transport = _GatewayTransport(
                    self.capture["tools"],
                    version=version,
                    server_name=server_name,
                    protocol_version=protocol_version,
                )
                gateway = UpstreamReadGateway()
                gateway.configure(
                    _settings(self.signer.public_key_base64),
                    transport=transport,
                    release_registry=self.compiled,
                    signed_release_registry=self._registry(raw),
                )
                snapshot = await gateway.initialize(
                    FastMCP("signed-readmission-identity-refusal")
                )
                self.assertEqual(snapshot["dynamically_exposed_count"], 0)
                self.assertEqual(snapshot["fallback_count"], 0)
                self.assertEqual(transport.calls, 0)

    async def test_same_session_commit_occurs_once_after_final_validation(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        gateway, transport, snapshot = await self._initialize(
            raw=self._raw(entry=entry),
            version=version,
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        tool = gateway._registered_tool_registry.snapshot()[
            "ha_list_services"
        ]
        await tool.run({})
        self.assertEqual(transport.calls, 1)
        lifecycle = gateway.health_snapshot()["automatic_readmission"]
        self.assertEqual(lifecycle["issued_lease_count"], 0)
        self.assertEqual(lifecycle["active_commit_count"], 0)
        self.assertEqual(lifecycle["fallback_count"], 0)

    async def test_expiry_and_target_drift_stop_before_tools_call(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        gateway, transport, _snapshot = await self._initialize(
            raw=self._raw(entry=entry),
            version=version,
        )
        tool = gateway._registered_tool_registry.snapshot()[
            "ha_list_services"
        ]

        self.now = self.now + timedelta(days=2)
        await tool.run({})
        self.assertEqual(transport.calls, 0)
        self.assertEqual(
            gateway._registered_tool_registry.snapshot(),
            {},
        )

        # A fresh gateway retains positive authority but sees selected-target
        # contract drift in the same MCP exchange. Validation rejects before
        # the single-use lease can commit or tools/call can occur.
        self.now = self.now - timedelta(days=2)
        gateway, transport, _snapshot = await self._initialize(
            raw=self._raw(entry=entry),
            version=version,
        )
        tool = gateway._registered_tool_registry.snapshot()[
            "ha_list_services"
        ]
        transport.catalog = McpReadCatalog(
            protocol_version=transport.catalog.protocol_version,
            server_name=transport.catalog.server_name,
            server_version=transport.catalog.server_version,
            tools=tuple(
                {
                    **deepcopy(item),
                    **(
                        {"inputSchema": "invalid"}
                        if item.get("name") == "ha_list_services"
                        else {}
                    ),
                }
                for item in transport.catalog.tools
            ),
            connection_latency_ms=1.0,
            session_id=transport.catalog.session_id,
            catalog_complete=True,
        )
        await tool.run({})
        self.assertEqual(transport.calls, 0)
        self.assertEqual(
            gateway._registered_tool_registry.snapshot(),
            {},
        )
        health = gateway.health_snapshot()
        self.assertEqual(
            health["reconciliation_status"],
            "reprobe_requested",
        )
        self.assertEqual(health["dynamically_exposed_count"], 0)

    async def test_health_and_audit_projections_are_bounded_and_sanitized(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        gateway, _transport, snapshot = await self._initialize(
            raw=self._raw(entry=entry),
            version=version,
        )
        automatic = snapshot["automatic_readmission"]
        registry = snapshot["automatic_readmission_registry"]
        audit = gateway.readmission_audit_snapshot()
        encoded = json.dumps(
            {"automatic": automatic, "registry": registry, "audit": audit},
            sort_keys=True,
        )
        self.assertLess(len(encoded.encode("utf-8")), 32_768)
        self.assertEqual(automatic["fallback_count"], 0)
        self.assertEqual(automatic["authority_source"], "signed_registry")
        self.assertEqual(registry["registry_location"], "fixed_repository_https")
        self.assertNotIn(self.signer.public_key_base64, encoded)
        self.assertNotIn("signature", encoded)
        self.assertNotIn("synthetic-ha-mcp-session", encoded)
        self.assertNotIn(REGISTRY_URL, encoded)
        self.assertNotIn(entry["entry_id"], encoded)
        self.assertLessEqual(len(audit), 8)


if __name__ == "__main__":
    unittest.main()
