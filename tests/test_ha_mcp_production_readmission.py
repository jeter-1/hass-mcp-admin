from __future__ import annotations

import asyncio
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
    MAX_CACHE_BYTES,
    MAX_DENIAL_JOURNALS,
    PENDING_SCHEMA_VERSION,
    REGISTRY_URL,
    ReleaseRegistryOperationalError,
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
from ha_mcp_engineering.request_context import (  # noqa: E402
    begin_request,
    end_request,
)
from ha_mcp_engineering.signed_registry import (  # noqa: E402
    RegistryEnvelope,
    canonical_json,
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
    reviewed_entry,
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
    async def test_registry_http_reader_accumulates_fragmented_body_to_eof(self):
        class Content:
            def __init__(self, chunks):
                self.chunks = list(chunks)

            async def read(self, _maximum):
                return self.chunks.pop(0) if self.chunks else b""

        class Response:
            status = 200
            content_length = None

            def __init__(self, chunks):
                self.content = Content(chunks)

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

        class Session:
            def __init__(self, response, **_kwargs):
                self.response = response

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

            def get(self, _url, **_kwargs):
                return self.response

        registry = SignedReleaseRegistry(enabled=False, public_key="")
        response = Response([b"first", b"-", b"second"])
        with patch(
            "ha_mcp_engineering.ha_mcp_readmission.registry.aiohttp."
            "ClientSession",
            side_effect=lambda **kwargs: Session(response, **kwargs),
        ):
            raw = await registry._fetch_bytes(REGISTRY_URL, 32)
        self.assertEqual(raw, b"first-second")

    async def test_signed_journal_bootstrap_catchup_and_compaction(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)

        def chain(count, *, start=1, previous=None):
            result = []
            prior = previous
            for sequence in range(start, start + count):
                raw = signer.raw(
                    sequence=sequence,
                    previous_registry_sha256=prior,
                    generated_at=NOW + timedelta(seconds=sequence),
                    expires_at=NOW + timedelta(days=2),
                    entries=[],
                    revocations=[],
                )
                result.append(raw)
                prior = RegistryEnvelope.from_bytes(raw).content_digest
            return result

        first_three = chain(3)
        with tempfile.TemporaryDirectory() as directory:
            async def fresh_fetcher(_url, _maximum):
                return signer.journal_raw(envelopes=first_three)

            fresh = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=Path(directory) / "fresh.json",
                fetcher=fresh_fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await fresh.refresh())
            self.assertEqual(fresh.authority().sequence, 3)

            responses = [
                signer.journal_raw(envelopes=[first_three[0]]),
                signer.journal_raw(envelopes=first_three),
            ]

            async def fetcher(_url, _maximum):
                return responses.pop(0)

            catching_up = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=Path(directory) / "catchup.json",
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await catching_up.refresh())
            self.assertEqual(catching_up.authority().sequence, 1)
            self.assertTrue(await catching_up.refresh())
            self.assertEqual(catching_up.authority().sequence, 3)

            first_window = chain(64)
            sequence_64 = RegistryEnvelope.from_bytes(first_window[-1])
            sequence_65 = chain(
                1,
                start=65,
                previous=sequence_64.content_digest,
            )[0]
            windows = [
                signer.journal_raw(envelopes=first_window),
                signer.journal_raw(envelopes=[sequence_65]),
            ]

            async def window_fetcher(_url, _maximum):
                return windows.pop(0)

            compacted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=Path(directory) / "compacted.json",
                fetcher=window_fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await compacted.refresh())
            self.assertEqual(compacted.authority().sequence, 64)
            self.assertTrue(await compacted.refresh())
            self.assertEqual(compacted.authority().sequence, 65)

    async def test_corrupt_cache_recovers_only_from_authenticated_checkpoint(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        checkpoint = signer.raw(
            sequence=7,
            previous_registry_sha256="sha256:" + "1" * 64,
            generated_at=NOW,
            expires_at=NOW + timedelta(days=1),
            entries=[],
            revocations=[],
        )
        journal = signer.journal_raw(envelopes=[checkpoint])
        tampered = json.loads(journal)
        tampered["checkpoint_sequence"] = 6
        responses = [json.dumps(tampered).encode("utf-8"), journal]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            cache.write_bytes(b"corrupt")
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(registry.authority().surface_denied)
            self.assertFalse(await registry.refresh())
            self.assertTrue(registry.authority().surface_denied)
            self.assertTrue(await registry.refresh())
            self.assertEqual(registry.authority().sequence, 7)
            self.assertFalse(registry.authority().surface_denied)

    async def test_registry_and_cache_schema_versions_require_exact_integers(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        invalid_journals = []
        for invalid in (True, 1.0):
            unsigned = signer.journal_unsigned(
                sequence=1,
                entries=[],
                revocations=[],
            )
            unsigned["schema_version"] = invalid
            invalid_journals.append(
                canonical_json(signer.sign_mapping(unsigned))
            )

        with tempfile.TemporaryDirectory() as directory:
            responses = list(invalid_journals)

            async def fetcher(_url, _maximum):
                return responses.pop(0)

            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=Path(directory) / "cache.json",
                fetcher=fetcher,
                now=lambda: NOW,
            )
            for _invalid in invalid_journals:
                self.assertFalse(await registry.refresh())
                self.assertFalse(
                    registry.authority().positive_authority_current
                )

        valid_journal = json.loads(
            signer.journal_raw(
                sequence=1,
                entries=[],
                revocations=[],
            )
        )
        for suffix, document in (
            (
                "cache",
                {
                    "schema_version": 3.0,
                    "authority_journal": valid_journal,
                },
            ),
            (
                "pending",
                {
                    "schema_version": 3.0,
                    "candidate_journal": valid_journal,
                    "retained_denial_journals": [],
                },
            ),
        ):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory) / "cache.json"
                target = (
                    cache
                    if suffix == "cache"
                    else cache.with_name(cache.name + ".pending")
                )
                target.write_bytes(canonical_json(document))
                restarted = SignedReleaseRegistry(
                    enabled=True,
                    public_key=signer.public_key_base64,
                    cache_path=cache,
                    now=lambda: NOW,
                )
                self.assertTrue(restarted.authority().surface_denied)
                self.assertFalse(
                    restarted.authority().positive_authority_current
                )

    async def test_lifecycle_witness_is_strict_bounded_and_hash_bound(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        raw = signer.journal_raw(
            sequence=1,
            entries=[],
            revocations=[],
        )
        for label in (
            "missing",
            "malformed",
            "oversized",
            "refreshing",
            "mismatched",
            "duplicate_key",
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                async def fetcher(_url, _maximum):
                    return raw

                cache = Path(directory) / "cache.json"
                writer = SignedReleaseRegistry(
                    enabled=True,
                    public_key=signer.public_key_base64,
                    cache_path=cache,
                    fetcher=fetcher,
                    now=lambda: NOW,
                )
                self.assertTrue(await writer.refresh())
                witness = writer._lifecycle_witness_path()
                digest = writer._authority_journal.content_digest
                if label == "missing":
                    witness.unlink()
                elif label == "malformed":
                    witness.write_bytes(b"not-json")
                elif label == "oversized":
                    witness.write_bytes(b"x" * 513)
                elif label == "refreshing":
                    witness.write_bytes(
                        canonical_json(
                            {
                                "schema_version": 1,
                                "state": "refreshing",
                                "authority_journal_sha256": digest,
                            }
                        )
                    )
                elif label == "mismatched":
                    witness.write_bytes(
                        canonical_json(
                            {
                                "schema_version": 1,
                                "state": "committed",
                                "authority_journal_sha256": (
                                    "sha256:" + "0" * 64
                                ),
                            }
                        )
                    )
                else:
                    witness.write_bytes(
                        (
                            '{"schema_version":1,"schema_version":1,'
                            '"state":"committed",'
                            f'"authority_journal_sha256":"{digest}"}}'
                        ).encode("utf-8")
                    )

                restarted = SignedReleaseRegistry(
                    enabled=True,
                    public_key=signer.public_key_base64,
                    cache_path=cache,
                    now=lambda: NOW,
                )
                self.assertTrue(restarted.authority().surface_denied)
                self.assertFalse(
                    restarted.authority().positive_authority_current
                )
                self.assertEqual(
                    restarted.snapshot()["cache_status"],
                    "invalid",
                )

    async def test_empty_bootstrap_witness_distinguishes_outage_from_denial(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)

        async def unavailable(_url, _maximum):
            raise OSError("synthetic registry outage")

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=unavailable,
                now=lambda: NOW,
            )
            self.assertFalse(await registry.refresh())
            self.assertFalse(registry.authority().surface_denied)
            self.assertEqual(registry.snapshot()["cache_status"], "missing")
            self.assertTrue(registry._lifecycle_witness_path().exists())

            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=unavailable,
                now=lambda: NOW,
            )
            self.assertFalse(restarted.authority().surface_denied)
            self.assertEqual(restarted.snapshot()["cache_status"], "missing")

            witness = restarted._lifecycle_witness_path()
            witness.write_bytes(
                witness.read_bytes().replace(
                    b'"committed"', b'"refreshing"'
                )
            )
            interrupted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=unavailable,
                now=lambda: NOW,
            )
            self.assertTrue(interrupted.authority().surface_denied)
            self.assertEqual(
                interrupted.snapshot()["cache_status"], "invalid"
            )

        invalid_witnesses = {
            "malformed": b"not-json",
            "oversized": b"x" * 513,
            "mismatched": canonical_json(
                {
                    "schema_version": 1,
                    "state": "committed",
                    "authority_journal_sha256": "sha256:" + "0" * 64,
                }
            ),
            "duplicate_key": (
                '{"schema_version":1,"schema_version":1,'
                '"state":"committed",'
                '"authority_journal_sha256":"sha256:'
                + "0" * 64
                + '"}'
            ).encode("utf-8"),
        }
        for label, payload in invalid_witnesses.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory) / "cache.json"
                cache.with_name(cache.name + ".lifecycle").write_bytes(
                    payload
                )
                invalid = SignedReleaseRegistry(
                    enabled=True,
                    public_key=signer.public_key_base64,
                    cache_path=cache,
                    fetcher=unavailable,
                    now=lambda: NOW,
                )
                self.assertTrue(invalid.authority().surface_denied)
                self.assertEqual(
                    invalid.snapshot()["cache_status"], "invalid"
                )

        with tempfile.TemporaryDirectory() as directory:
            genuinely_fresh = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=Path(directory) / "cache.json",
                fetcher=unavailable,
                now=lambda: NOW,
            )
            self.assertFalse(genuinely_fresh.authority().surface_denied)
            self.assertEqual(
                genuinely_fresh.snapshot()["cache_status"], "missing"
            )

    async def test_first_bootstrap_revocation_storage_failure_is_durable_denial(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        active_version = "8.2.0"
        raw = signer.journal_raw(
            sequence=1,
            entries=[],
            revocations=[
                revocation(
                    entry_id="ha-mcp-v8.2.0-synthetic",
                    version=active_version,
                )
            ],
        )
        fetched = False

        async def fetcher(_url, _maximum):
            nonlocal fetched
            fetched = True
            return raw

        original_temporary = tempfile.NamedTemporaryFile

        def fail_candidate_storage(*args, **kwargs):
            if fetched and ".pending." in kwargs.get("prefix", ""):
                raise OSError("synthetic cache volume outage")
            return original_temporary(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            with patch(
                "ha_mcp_engineering.ha_mcp_readmission.registry."
                "tempfile.NamedTemporaryFile",
                side_effect=fail_candidate_storage,
            ):
                self.assertFalse(await registry.refresh())
            self.assertTrue(registry.authority().surface_denied)
            self.assertTrue(
                registry.authority().revoked("ha-mcp", active_version)
            )
            self.assertFalse(cache.exists())
            self.assertTrue(registry._lifecycle_witness_path().exists())

            async def unavailable(_url, _maximum):
                raise OSError("synthetic registry outage")

            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=unavailable,
                now=lambda: NOW,
            )
            self.assertTrue(restarted.authority().surface_denied)
            self.assertFalse(await restarted.refresh())
            self.assertTrue(restarted.authority().surface_denied)

    async def test_missing_main_cache_with_committed_witness_is_denial(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        raw = signer.journal_raw(
            sequence=1,
            entries=[],
            revocations=[
                revocation(
                    entry_id="ha-mcp-v8.2.0-synthetic",
                    version="8.2.0",
                )
            ],
        )

        async def fetcher(_url, _maximum):
            return raw

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
            self.assertTrue(cache.exists())
            cache.unlink()

            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                now=lambda: NOW,
            )
            self.assertTrue(restarted.authority().surface_denied)
            self.assertFalse(
                restarted.authority().positive_authority_current
            )
            self.assertEqual(
                restarted.snapshot()["cache_status"], "invalid"
            )

    async def test_journal_intermediate_refusal_matrix_and_no_tip_shortcut(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        first = signer.raw(sequence=1, entries=[], revocations=[])
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=NOW,
            entries=[],
            revocations=[],
        )
        second_digest = RegistryEnvelope.from_bytes(second).content_digest
        third = signer.raw(
            sequence=3,
            previous_registry_sha256=second_digest,
            generated_at=NOW + timedelta(seconds=1),
            entries=[],
            revocations=[],
        )
        disconnected = signer.raw(
            sequence=2,
            previous_registry_sha256="sha256:" + "0" * 64,
            generated_at=NOW,
            entries=[],
            revocations=[],
        )
        conflict = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=NOW,
            entries=[],
            revocations=[revocation()],
        )
        cases = {
            "missing": signer.journal_raw(envelopes=[first, third]),
            "disconnected": signer.journal_raw(
                envelopes=[first, disconnected]
            ),
            "duplicate": signer.journal_raw(
                envelopes=[first, second, second]
            ),
            "conflicting": signer.journal_raw(
                envelopes=[first, second, conflict]
            ),
            "capacity": signer.journal_raw(
                envelopes=[first] * 65
            ),
            "bare_tip": third,
        }
        for label, raw in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                async def fetcher(_url, _maximum, value=raw):
                    return value

                registry = SignedReleaseRegistry(
                    enabled=True,
                    public_key=signer.public_key_base64,
                    cache_path=Path(directory) / "cache.json",
                    fetcher=fetcher,
                    now=lambda: NOW,
                )
                self.assertFalse(await registry.refresh())
                self.assertIsNone(registry.authority().envelope)

    async def test_skipped_revocation_is_retained_across_restart(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        first = signer.raw(sequence=1, entries=[], revocations=[])
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=NOW,
            entries=[],
            revocations=[revocation(version="8.2.0")],
        )
        second_digest = RegistryEnvelope.from_bytes(second).content_digest
        third = signer.raw(
            sequence=3,
            previous_registry_sha256=second_digest,
            generated_at=NOW + timedelta(seconds=1),
            entries=[],
            revocations=[],
        )
        journal = signer.journal_raw(envelopes=[first, second, third])
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"

            async def fetcher(_url, _maximum):
                return journal

            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await registry.refresh())
            self.assertTrue(registry.authority().revoked("ha-mcp", "8.2.0"))
            fifth = signer.raw(
                sequence=5,
                previous_registry_sha256="sha256:" + "4" * 64,
                generated_at=NOW + timedelta(seconds=2),
                entries=[],
                revocations=[],
            )
            compacted = signer.journal_raw(
                envelopes=[fifth],
                revocation_sources=[second],
            )

            async def compacted_fetcher(_url, _maximum):
                return compacted

            registry._fetcher = compacted_fetcher
            self.assertTrue(await registry.refresh())
            self.assertEqual(registry.authority().sequence, 5)
            self.assertTrue(registry.authority().revoked("ha-mcp", "8.2.0"))
            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                now=lambda: NOW,
            )
            self.assertEqual(restarted.authority().sequence, 5)
            self.assertTrue(restarted.authority().revoked("ha-mcp", "8.2.0"))

    async def test_revocation_source_capacity_denies_old_authority_and_restart(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        active_version = "8.2.1"
        chain = []
        previous = None
        for sequence in range(1, 11):
            revoked_version = (
                active_version if sequence == 10 else f"8.1.{sequence}"
            )
            raw = signer.raw(
                sequence=sequence,
                previous_registry_sha256=previous,
                generated_at=NOW + timedelta(seconds=sequence),
                expires_at=NOW + timedelta(days=1),
                entries=(
                    [
                        reviewed_entry(
                            entry_id="ha-mcp-v8.2.1-synthetic",
                            version=active_version,
                        )
                    ]
                    if sequence == 1
                    else []
                ),
                revocations=(
                    []
                    if sequence == 1
                    else [
                        revocation(
                            entry_id=f"ha-mcp-{revoked_version}-synthetic",
                            version=revoked_version,
                        )
                    ]
                ),
            )
            chain.append(raw)
            previous = RegistryEnvelope.from_bytes(raw).content_digest

        responses = [
            signer.journal_raw(envelopes=[chain[0]]),
            signer.journal_raw(envelopes=chain),
        ]
        attempted_restore = signer.raw(
            sequence=11,
            previous_registry_sha256=previous,
            generated_at=NOW + timedelta(seconds=11),
            expires_at=NOW + timedelta(days=1),
            entries=[
                reviewed_entry(
                    entry_id="ha-mcp-v8.2.1-synthetic",
                    version=active_version,
                )
            ],
            revocations=[],
        )
        responses.append(
            signer.journal_raw(envelopes=[attempted_restore])
        )
        attempted_restore_digest = RegistryEnvelope.from_bytes(
            attempted_restore
        ).content_digest
        safe_version = "8.2.2"
        corrected = signer.raw(
            sequence=12,
            previous_registry_sha256=attempted_restore_digest,
            generated_at=NOW + timedelta(seconds=12),
            expires_at=NOW + timedelta(days=1),
            entries=[
                reviewed_entry(
                    entry_id="ha-mcp-v8.2.2-synthetic",
                    version=safe_version,
                )
            ],
            revocations=[
                *[
                    revocation(
                        entry_id=f"ha-mcp-8.1.{sequence}-synthetic",
                        version=f"8.1.{sequence}",
                    )
                    for sequence in range(2, 10)
                ],
                revocation(
                    entry_id=f"ha-mcp-{active_version}-synthetic",
                    version=active_version,
                ),
            ],
        )
        responses.append(signer.journal_raw(envelopes=[corrected]))

        async def fetcher(_url, _maximum):
            return responses.pop(0)

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
            self.assertIsNotNone(
                registry.authority().entry_for("ha-mcp", active_version)
            )
            self.assertFalse(await registry.refresh())
            authority = registry.authority()
            self.assertEqual(authority.sequence, 10)
            self.assertTrue(authority.surface_denied)
            self.assertFalse(authority.positive_authority_current)
            self.assertTrue(authority.revoked("ha-mcp", active_version))
            self.assertEqual(
                registry.snapshot()["last_failure_reason"],
                "registry_revocation_history_capacity_exhausted",
            )
            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertEqual(restarted.authority().sequence, 10)
            self.assertTrue(restarted.authority().surface_denied)
            self.assertFalse(
                restarted.authority().positive_authority_current
            )
            self.assertTrue(
                restarted.authority().revoked("ha-mcp", active_version)
            )

            # A later signed checkpoint cannot omit retained denial evidence
            # and thereby restore the previously revoked release.
            self.assertFalse(await restarted.refresh())
            self.assertEqual(restarted.authority().sequence, 11)
            self.assertTrue(restarted.authority().surface_denied)
            self.assertFalse(
                restarted.authority().positive_authority_current
            )
            self.assertTrue(
                restarted.authority().revoked("ha-mcp", active_version)
            )
            second_restart = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertEqual(second_restart.authority().sequence, 11)
            self.assertTrue(second_restart.authority().surface_denied)
            self.assertFalse(
                second_restart.authority().positive_authority_current
            )
            self.assertTrue(
                second_restart.authority().revoked(
                    "ha-mcp",
                    active_version,
                )
            )

            # A later linked checkpoint may recover global availability only
            # after it carries every retained denial in a representable form.
            self.assertTrue(await second_restart.refresh())
            recovered = second_restart.authority()
            self.assertEqual(recovered.sequence, 12)
            self.assertFalse(recovered.surface_denied)
            self.assertTrue(recovered.positive_authority_current)
            self.assertTrue(recovered.revoked("ha-mcp", active_version))
            self.assertIsNotNone(
                recovered.entry_for("ha-mcp", safe_version)
            )

    async def test_denial_journal_overflow_is_permanent_across_restart(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        active_version = "8.2.1"
        active_entry = reviewed_entry(
            entry_id="ha-mcp-v8.2.1-synthetic",
            version=active_version,
        )
        first = signer.raw(
            sequence=1,
            entries=[active_entry],
            revocations=[],
        )
        previous = RegistryEnvelope.from_bytes(first).content_digest
        candidates = []
        for sequence in range(2, MAX_DENIAL_JOURNALS + 3):
            is_overflow_candidate = (
                sequence == MAX_DENIAL_JOURNALS + 2
            )
            revoked_version = (
                active_version
                if is_overflow_candidate
                else f"8.0.{sequence}"
            )
            candidate = signer.raw(
                sequence=sequence,
                previous_registry_sha256=previous,
                generated_at=NOW + timedelta(seconds=sequence),
                expires_at=NOW + timedelta(days=1),
                entries=[],
                revocations=[
                    revocation(
                        entry_id=(
                            active_entry["entry_id"]
                            if is_overflow_candidate
                            else f"ha-mcp-8.0.{sequence}-synthetic"
                        ),
                        version=revoked_version,
                    )
                ],
            )
            candidates.append(candidate)
            previous = RegistryEnvelope.from_bytes(
                candidate
            ).content_digest

        recovery_sequence = MAX_DENIAL_JOURNALS + 3
        recovery = signer.raw(
            sequence=recovery_sequence,
            previous_registry_sha256=previous,
            generated_at=NOW + timedelta(seconds=recovery_sequence),
            expires_at=NOW + timedelta(days=1),
            entries=[active_entry],
            revocations=[
                revocation(
                    entry_id=f"ha-mcp-8.0.{sequence}-synthetic",
                    version=f"8.0.{sequence}",
                )
                for sequence in range(2, MAX_DENIAL_JOURNALS + 2)
            ],
        )
        recovery_raw = signer.journal_raw(envelopes=[recovery])
        responses = [
            signer.journal_raw(envelopes=[first]),
            *[
                signer.journal_raw(envelopes=[candidate])
                for candidate in candidates
            ],
            recovery_raw,
            recovery_raw,
            recovery_raw,
        ]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

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
            self.assertTrue(await registry.refresh())
            for _candidate in candidates[1:]:
                self.assertFalse(await registry.refresh())
            self.assertEqual(
                registry.authority().sequence,
                MAX_DENIAL_JOURNALS + 2,
            )
            self.assertTrue(registry.authority().surface_denied)
            self.assertTrue(registry._volatile_revocation_overflow)

            self.assertFalse(await registry.refresh())
            self.assertFalse(await registry.refresh())
            self.assertTrue(registry.authority().surface_denied)
            self.assertFalse(
                registry.authority().positive_authority_current
            )
            self.assertTrue(registry._volatile_revocation_overflow)

            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertEqual(
                restarted.authority().sequence,
                MAX_DENIAL_JOURNALS + 2,
            )
            self.assertTrue(restarted.authority().surface_denied)
            self.assertTrue(restarted._volatile_revocation_overflow)
            self.assertFalse(await restarted.refresh())
            self.assertTrue(restarted.authority().surface_denied)
            self.assertFalse(
                restarted.authority().positive_authority_current
            )
            self.assertIsNone(
                restarted.authority().entry_for(
                    "ha-mcp",
                    active_version,
                )
            )

    async def test_write_ahead_witness_blocks_composed_storage_outage(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        active_version = "8.2.1"
        entry = reviewed_entry(
            entry_id="ha-mcp-v8.2.1-synthetic",
            version=active_version,
        )
        first = signer.raw(sequence=1, entries=[entry], revocations=[])
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(days=1),
            entries=[],
            revocations=[
                revocation(
                    entry_id=entry["entry_id"],
                    version=active_version,
                )
            ],
        )
        outage_active = False
        fetch_count = 0

        async def fetcher(_url, _maximum):
            nonlocal fetch_count, outage_active
            fetch_count += 1
            if fetch_count == 1:
                return signer.journal_raw(envelopes=[first])
            outage_active = True
            return signer.journal_raw(envelopes=[second])

        original_replace = os.replace
        original_temporary = tempfile.NamedTemporaryFile

        def fail_storage_replace(source, target):
            if outage_active:
                raise OSError("synthetic cache volume outage")
            return original_replace(source, target)

        def fail_pending_temporary(*args, **kwargs):
            if outage_active and ".pending." in kwargs.get("prefix", ""):
                raise OSError("synthetic cache volume outage")
            return original_temporary(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            with patch(
                "ha_mcp_engineering.ha_mcp_readmission.registry.os.replace",
                side_effect=fail_storage_replace,
            ), patch(
                "ha_mcp_engineering.ha_mcp_readmission.registry."
                "tempfile.NamedTemporaryFile",
                side_effect=fail_pending_temporary,
            ):
                self.assertTrue(await registry.refresh())
                self.assertFalse(await registry.refresh())

            authority = registry.authority()
            self.assertTrue(authority.surface_denied)
            self.assertTrue(authority.revoked("ha-mcp", active_version))
            self.assertTrue(cache.exists())
            self.assertFalse(
                cache.with_name(cache.name + ".pending").exists()
            )
            self.assertFalse(
                cache.with_name(cache.name + ".previous").exists()
            )

            stale = signer.journal_raw(envelopes=[first])

            async def stale_fetcher(_url, _maximum):
                return stale

            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=stale_fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(restarted.authority().surface_denied)
            self.assertFalse(
                restarted.authority().positive_authority_current
            )
            self.assertFalse(await restarted.refresh())
            self.assertTrue(restarted.authority().surface_denied)
            self.assertFalse(
                restarted.authority().positive_authority_current
            )

    async def test_stale_pending_cannot_replace_later_revocation(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        active_version = "8.2.1"
        entry = reviewed_entry(
            entry_id="ha-mcp-v8.2.1-synthetic",
            version=active_version,
        )
        first = signer.raw(sequence=1, entries=[entry], revocations=[])
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(days=1),
            entries=[entry],
            revocations=[],
        )
        second_digest = RegistryEnvelope.from_bytes(second).content_digest
        third = signer.raw(
            sequence=3,
            previous_registry_sha256=second_digest,
            generated_at=NOW + timedelta(seconds=2),
            expires_at=NOW + timedelta(days=1),
            entries=[],
            revocations=[
                revocation(
                    entry_id=entry["entry_id"],
                    version=active_version,
                )
            ],
        )
        responses = [
            signer.journal_raw(envelopes=[first]),
            signer.journal_raw(envelopes=[second]),
            signer.journal_raw(envelopes=[third]),
        ]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            pending = cache.with_name(cache.name + ".pending")
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await registry.refresh())

            original_unlink = Path.unlink

            def retain_pending(path, *args, **kwargs):
                if path == pending:
                    raise OSError("synthetic pending cleanup failure")
                return original_unlink(path, *args, **kwargs)

            with patch.object(
                Path,
                "unlink",
                autospec=True,
                side_effect=retain_pending,
            ):
                self.assertTrue(await registry.refresh())
            self.assertTrue(cache.exists())
            self.assertTrue(pending.exists())
            committed_restart = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                now=lambda: NOW,
            )
            self.assertTrue(
                committed_restart.authority().positive_authority_current
            )
            self.assertEqual(committed_restart.authority().sequence, 2)

            original_replace = os.replace

            def reject_pending_replacement(source, target):
                if Path(target) == pending:
                    raise OSError("synthetic pending replacement failure")
                return original_replace(source, target)

            with patch(
                "ha_mcp_engineering.ha_mcp_readmission.registry.os.replace",
                side_effect=reject_pending_replacement,
            ):
                self.assertFalse(await registry.refresh())
            self.assertFalse(cache.exists())
            self.assertTrue(pending.exists())
            self.assertTrue(
                cache.with_name(cache.name + ".previous").exists()
            )

            alternate = signer.raw(
                sequence=3,
                previous_registry_sha256=second_digest,
                generated_at=NOW + timedelta(seconds=2),
                expires_at=NOW + timedelta(days=1),
                entries=[entry],
                revocations=[],
            )
            alternate_raw = signer.journal_raw(envelopes=[alternate])

            async def alternate_fetcher(_url, _maximum):
                return alternate_raw

            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=alternate_fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(restarted.authority().surface_denied)
            self.assertTrue(restarted._volatile_revocation_overflow)
            for _attempt in range(2):
                self.assertFalse(await restarted.refresh())
            self.assertTrue(restarted.authority().surface_denied)
            self.assertFalse(
                restarted.authority().positive_authority_current
            )
            self.assertIsNone(
                restarted.authority().entry_for(
                    "ha-mcp",
                    active_version,
                )
            )

    async def test_marker_failure_boundaries_retire_positive_cache_first(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        active_version = "8.2.1"
        entry = reviewed_entry(
            entry_id="ha-mcp-v8.2.1-synthetic",
            version=active_version,
        )
        first = signer.raw(
            sequence=1,
            entries=[entry],
            revocations=[],
        )
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(days=1),
            entries=[],
            revocations=[
                revocation(
                    entry_id=entry["entry_id"],
                    version=active_version,
                )
            ],
        )
        initial = signer.journal_raw(envelopes=[first])
        revoked = signer.journal_raw(envelopes=[first, second])
        alternate_second = signer.journal_raw(
            envelopes=[
                signer.raw(
                    sequence=2,
                    previous_registry_sha256=first_digest,
                    generated_at=NOW + timedelta(seconds=1),
                    expires_at=NOW + timedelta(days=1),
                    entries=[entry],
                    revocations=[],
                )
            ]
        )
        attempted_restore = signer.journal_raw(
            envelopes=[
                signer.raw(
                    sequence=3,
                    previous_registry_sha256=(
                        RegistryEnvelope.from_bytes(second).content_digest
                    ),
                    generated_at=NOW + timedelta(seconds=2),
                    expires_at=NOW + timedelta(days=1),
                    entries=[entry],
                    revocations=[],
                )
            ]
        )

        for boundary in (
            "marker_create",
            "marker_file_fsync",
            "marker_replace",
            "marker_directory_fsync",
            "retirement_replace",
            "retirement_directory_fsync",
        ):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                responses = [initial, revoked]
                restart_responses = [
                    initial,
                    alternate_second,
                    attempted_restore,
                ]

                async def fetcher(_url, _maximum):
                    return responses.pop(0)

                async def restore_fetcher(_url, _maximum):
                    return restart_responses.pop(0)

                cache = Path(directory) / "cache.json"
                registry = SignedReleaseRegistry(
                    enabled=True,
                    public_key=signer.public_key_base64,
                    cache_path=cache,
                    fetcher=fetcher,
                    now=lambda: NOW,
                )
                self.assertTrue(await registry.refresh())
                self.assertTrue(cache.exists())

                if boundary == "marker_create":
                    failure = patch(
                        "ha_mcp_engineering.ha_mcp_readmission.registry."
                        "tempfile.NamedTemporaryFile",
                        side_effect=OSError("synthetic marker create failure"),
                    )
                elif boundary == "marker_file_fsync":
                    original_fsync = os.fsync
                    fsync_calls = 0

                    def fail_marker_file_fsync(fd):
                        nonlocal fsync_calls
                        fsync_calls += 1
                        if fsync_calls == 2:
                            raise OSError("synthetic marker fsync failure")
                        return original_fsync(fd)

                    failure = patch(
                        "ha_mcp_engineering.ha_mcp_readmission.registry."
                        "os.fsync",
                        side_effect=fail_marker_file_fsync,
                    )
                elif boundary == "marker_replace":
                    original_replace = os.replace
                    replace_calls = 0

                    def fail_marker_replace(source, target):
                        nonlocal replace_calls
                        replace_calls += 1
                        if replace_calls == 2:
                            raise OSError("synthetic marker replace failure")
                        return original_replace(source, target)

                    failure = patch(
                        "ha_mcp_engineering.ha_mcp_readmission.registry."
                        "os.replace",
                        side_effect=fail_marker_replace,
                    )
                elif boundary == "marker_directory_fsync":
                    failure = patch.object(
                        SignedReleaseRegistry,
                        "_fsync_directory",
                        side_effect=[
                            None,
                            OSError("synthetic marker directory failure"),
                        ],
                    )
                elif boundary == "retirement_replace":
                    original_replace = os.replace

                    def fail_retirement_replace(source, target):
                        if Path(target) == cache.with_name(
                            cache.name + ".previous"
                        ):
                            raise OSError(
                                "synthetic retirement replace failure"
                            )
                        return original_replace(source, target)

                    failure = patch(
                        "ha_mcp_engineering.ha_mcp_readmission.registry."
                        "os.replace",
                        side_effect=fail_retirement_replace,
                    )
                else:
                    original_fsync_directory = (
                        SignedReleaseRegistry._fsync_directory
                    )
                    fsync_directory_calls = 0

                    def fail_retirement_directory_fsync(parent):
                        nonlocal fsync_directory_calls
                        fsync_directory_calls += 1
                        if fsync_directory_calls == 1:
                            raise OSError(
                                "synthetic retirement directory failure"
                            )
                        return original_fsync_directory(parent)

                    failure = patch.object(
                        SignedReleaseRegistry,
                        "_fsync_directory",
                        side_effect=fail_retirement_directory_fsync,
                    )

                with failure:
                    self.assertFalse(await registry.refresh())
                self.assertTrue(registry.authority().surface_denied)
                self.assertFalse(
                    registry.authority().positive_authority_current
                )
                if boundary == "retirement_replace":
                    self.assertTrue(cache.exists())
                    self.assertTrue(
                        cache.with_name(cache.name + ".pending").exists()
                    )
                else:
                    self.assertFalse(cache.exists())
                    self.assertTrue(
                        cache.with_name(cache.name + ".previous").exists()
                    )

                restarted = SignedReleaseRegistry(
                    enabled=True,
                    public_key=signer.public_key_base64,
                    cache_path=cache,
                    fetcher=restore_fetcher,
                    now=lambda: NOW,
                )
                self.assertTrue(restarted.authority().surface_denied)
                self.assertFalse(
                    restarted.authority().positive_authority_current
                )
                self.assertFalse(await restarted.refresh())
                self.assertIn(
                    restarted.snapshot()["last_failure_reason"],
                    {
                        "registry_journal_disconnected",
                        "registry_sequence_rollback",
                    },
                )
                self.assertFalse(
                    restarted.authority().positive_authority_current
                )
                self.assertFalse(await restarted.refresh())
                self.assertFalse(
                    restarted.authority().positive_authority_current
                )
                self.assertFalse(await restarted.refresh())
                self.assertIn(
                    restarted.snapshot()["last_failure_reason"],
                    {
                        "registry_journal_disconnected",
                        "registry_previous_digest_mismatch",
                        "registry_revocation_history_missing",
                    },
                )
                self.assertFalse(
                    restarted.authority().positive_authority_current
                )

    async def test_failed_candidate_is_sequence_barrier_until_chained_success(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        first = signer.raw(sequence=1, entries=[], revocations=[])
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=NOW,
            entries=[],
            revocations=[revocation(version="8.2.0")],
        )
        conflict = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=NOW,
            entries=[],
            revocations=[],
        )
        second_digest = RegistryEnvelope.from_bytes(second).content_digest
        third = signer.raw(
            sequence=3,
            previous_registry_sha256=second_digest,
            generated_at=NOW + timedelta(seconds=1),
            entries=[],
            revocations=[],
        )
        responses = [
            signer.journal_raw(envelopes=[first]),
            signer.journal_raw(envelopes=[first, second]),
            signer.journal_raw(envelopes=[first, conflict]),
            signer.journal_raw(envelopes=[second, third]),
        ]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache.json"
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache_path,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await registry.refresh())
            with patch.object(
                registry,
                "_write_cache",
                side_effect=ReleaseRegistryOperationalError(
                    "registry_cache_write_failed"
                ),
            ):
                self.assertFalse(await registry.refresh())
            self.assertEqual(registry.authority().sequence, 2)
            self.assertTrue(registry.authority().surface_denied)
            self.assertFalse(await registry.refresh())
            self.assertEqual(
                registry.snapshot()["last_failure_reason"],
                "registry_sequence_replay_conflict",
            )
            self.assertTrue(await registry.refresh())
            self.assertEqual(registry.authority().sequence, 3)
            self.assertFalse(registry.authority().surface_denied)
            self.assertTrue(registry.authority().revoked("ha-mcp", "8.2.0"))

    async def test_failed_preinstall_fsync_cleans_temporary_files(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)

        async def fetcher(_url, _maximum):
            return signer.journal_raw(
                sequence=1,
                entries=[],
                revocations=[],
            )

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
                side_effect=OSError("synthetic fsync failure"),
            ):
                self.assertFalse(await registry.refresh())
            self.assertEqual(list(Path(directory).iterdir()), [])

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
        responses = [
            signer.journal_raw(envelopes=[first]),
            signer.journal_raw(envelopes=[first, second]),
        ]

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
        responses = [
            signer.journal_raw(envelopes=[first]),
            signer.journal_raw(envelopes=[first, second]),
            signer.journal_raw(envelopes=[first]),
            signer.journal_raw(envelopes=[first, conflict]),
            b"not-json",
        ]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache.json"
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache_path,
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
                "registry_journal_invalid",
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
        responses = [
            trusted.journal_raw(envelopes=[expired]),
            unknown.journal_raw(envelopes=[unknown_key]),
            trusted.journal_raw(envelopes=[tampered]),
        ]

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
            self.assertTrue(registry.authority().surface_denied)
            self.assertEqual(registry.snapshot()["cache_status"], "invalid")

    async def test_restart_rejects_conflicting_and_disconnected_cache_topology(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        first = signer.raw(
            sequence=1,
            entries=[],
            revocations=[revocation(version="7.14.2")],
        )
        first_envelope = RegistryEnvelope.from_bytes(first)
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_envelope.content_digest,
            entries=[],
            revocations=[],
            generated_at=NOW,
        )
        conflicting = signer.raw(
            sequence=2,
            previous_registry_sha256=first_envelope.content_digest,
            entries=[],
            revocations=[revocation()],
            generated_at=NOW,
        )
        disconnected = signer.raw(
            sequence=2,
            previous_registry_sha256="sha256:" + "0" * 64,
            entries=[],
            revocations=[],
            generated_at=NOW,
        )
        cases = {
            "equal_sequence_conflict": signer.sign_mapping(
                signer.journal_unsigned(
                    envelopes=[first, second],
                    revocation_sources=[conflicting],
                )
            ),
            "disconnected_chain": signer.sign_mapping(
                signer.journal_unsigned(
                    envelopes=[first, disconnected],
                )
            ),
            "duplicate_source": signer.sign_mapping(
                signer.journal_unsigned(
                    envelopes=[first, second],
                    revocation_sources=[first, first],
                )
            ),
        }
        valid = signer.sign_mapping(
            signer.journal_unsigned(envelopes=[first, second])
        )
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "valid.json"
            async def valid_fetcher(_url, _maximum):
                return canonical_json(valid)

            writer = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                fetcher=valid_fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await writer.refresh())
            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                now=lambda: NOW,
            )
            self.assertTrue(
                restarted.authority().positive_authority_current
            )

        for label, journal in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory) / "cache.json"
                cache.write_bytes(
                    canonical_json(
                        {
                            "schema_version": 3,
                            "authority_journal": journal,
                        }
                    )
                )
                registry = SignedReleaseRegistry(
                    enabled=True,
                    public_key=signer.public_key_base64,
                    cache_path=cache,
                    now=lambda: NOW,
                )
                self.assertFalse(
                    registry.authority().positive_authority_current
                )
                self.assertTrue(registry.authority().surface_denied)
                self.assertEqual(registry.snapshot()["cache_status"], "invalid")

    async def test_verified_revocation_remains_denial_only_on_cache_failure(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        first = signer.raw(sequence=1, entries=[], revocations=[])
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            entries=[],
            revocations=[revocation(version="8.2.0")],
            generated_at=NOW,
        )
        responses = [
            signer.journal_raw(envelopes=[first]),
            signer.journal_raw(envelopes=[first, second]),
        ]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache.json"
            registry = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache_path,
                fetcher=fetcher,
                now=lambda: NOW,
            )
            self.assertTrue(await registry.refresh())
            with patch.object(
                registry,
                "_write_cache",
                side_effect=ReleaseRegistryOperationalError(
                    "registry_cache_write_failed"
                ),
            ):
                self.assertFalse(await registry.refresh())
            authority = registry.authority()
            self.assertEqual(authority.envelope.sequence, 1)
            self.assertEqual(authority.sequence, 2)
            self.assertTrue(authority.surface_denied)
            self.assertTrue(authority.revoked("ha-mcp", "8.2.0"))
            self.assertEqual(
                registry.snapshot()["last_failure_reason"],
                "registry_cache_write_failed",
            )
            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache_path,
                now=lambda: NOW,
            )
            restarted_authority = restarted.authority()
            self.assertEqual(restarted_authority.sequence, 2)
            self.assertTrue(restarted_authority.surface_denied)
            self.assertTrue(
                restarted_authority.revoked("ha-mcp", "8.2.0")
            )
            self.assertFalse(
                restarted_authority.positive_authority_current
            )

    async def test_persistence_failure_never_publishes_candidate(self):
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)

        async def fetcher(_url, _maximum):
            return signer.journal_raw(
                sequence=1,
                entries=[],
                revocations=[],
            )

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
            return signer.journal_raw(
                sequence=1,
                entries=[],
                revocations=[],
            )

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
        responses = [
            signer.journal_raw(envelopes=[first]),
            signer.journal_raw(envelopes=[first, second]),
        ]

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
            self.assertEqual(registry.authority().envelope.sequence, 1)
            self.assertEqual(registry.authority().sequence, 2)
            self.assertTrue(registry.authority().surface_denied)

            restarted = SignedReleaseRegistry(
                enabled=True,
                public_key=signer.public_key_base64,
                cache_path=cache,
                now=lambda: NOW,
            )
            self.assertEqual(restarted.authority().envelope.sequence, 1)
            self.assertEqual(restarted.authority().sequence, 2)
            self.assertTrue(restarted.authority().surface_denied)

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
    first_name = next(
        item["tool_name"]
        for item in contracts
        if item["policy_classification"] == "automatic_read"
    )
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
        call_result: dict | None = None,
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
        self.call_result = call_result or {
            "content": [{"type": "text", "text": "{}"}],
            "isError": False,
        }

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
            call_result=deepcopy(self.call_result),
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
        return self.signer.journal_raw(
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
            schema_fingerprint(
                {"compatibility_entry_id": entry["entry_id"]}
            ),
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

    async def test_new_release_forces_one_bounded_refresh_without_restart(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        first = self.signer.raw(
            sequence=1,
            generated_at=self.now - timedelta(minutes=2),
            expires_at=self.now + timedelta(days=1),
            entries=[],
            revocations=[],
        )
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = self.signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(days=1),
            entries=[entry],
            revocations=[],
        )
        responses = [
            self.signer.journal_raw(envelopes=[first]),
            self.signer.journal_raw(envelopes=[first, second]),
        ]
        fetch_count = 0

        async def fetcher(url, maximum):
            nonlocal fetch_count
            self.assertEqual(url, REGISTRY_URL)
            fetch_count += 1
            raw = responses.pop(0)
            self.assertLessEqual(len(raw), maximum)
            return raw

        registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=Path(self.temporary.name) / "refresh-cache.json",
            fetcher=fetcher,
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
            signed_release_registry=registry,
        )
        snapshot = await gateway.initialize(
            FastMCP("signed-readmission-missing-release-refresh")
        )
        self.assertEqual(fetch_count, 2)
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        self.assertEqual(snapshot["readmission_authority_source"], "signed_registry")
        self.assertEqual(transport.calls, 0)

    async def test_missing_release_rearms_bounded_refresh_until_published(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        first = self.signer.raw(
            sequence=1,
            generated_at=self.now - timedelta(minutes=2),
            expires_at=self.now + timedelta(days=1),
            entries=[],
            revocations=[],
        )
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = self.signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(days=1),
            entries=[entry],
            revocations=[],
        )
        empty = self.signer.journal_raw(envelopes=[first])
        responses = [
            empty,
            empty,
            self.signer.journal_raw(envelopes=[first, second]),
        ]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=Path(self.temporary.name) / "retry-cache.json",
            fetcher=fetcher,
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
            signed_release_registry=registry,
        )
        server = FastMCP("signed-readmission-bounded-missing-retry")
        with (
            patch(
                "ha_mcp_engineering.ha_mcp_readmission.registry."
                "MISSING_RELEASE_REFRESH_INTERVAL_SECONDS",
                0.01,
            ),
            patch(
                "ha_mcp_engineering.providers.upstream_read_gateway."
                "MISSING_RELEASE_REFRESH_INTERVAL_SECONDS",
                0.01,
            ),
        ):
            first_snapshot = await gateway.initialize(server)
            self.assertEqual(first_snapshot["dynamically_exposed_count"], 0)
            await asyncio.wait_for(gateway._reprobe_event.wait(), timeout=1)
            gateway._reprobe_event.clear()
            second_snapshot = await gateway.initialize(server)
        self.assertEqual(second_snapshot["dynamically_exposed_count"], 25)
        self.assertEqual(len(responses), 0)
        self.assertEqual(transport.calls, 0)

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

    async def test_release_level_adapter_semantics_are_exactly_bound(self):
        version = "8.2.1"
        cases = {
            "error_contract_fingerprint": "0" * 64,
            "entity_lookup_missing_resource_status": (
                "deterministic_entity_not_found"
            ),
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                entry = _signed_entry_for(self.release, version=version)
                entry[field] = value
                _gateway, transport, snapshot = await self._initialize(
                    raw=self._raw(entry=entry),
                    version=version,
                )
                self.assertEqual(snapshot["dynamically_exposed_count"], 0)
                self.assertEqual(snapshot["fallback_count"], 0)
                self.assertEqual(transport.calls, 0)

    async def test_provider_constraint_drift_withholds_only_affected_read(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        constraint = entry["provider_argument_constraints"][0]
        affected = constraint["tool_name"]
        constraint["constraints_fingerprint"] = "0" * 64
        _gateway, transport, snapshot = await self._initialize(
            raw=self._raw(entry=entry),
            version=version,
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 24)
        self.assertNotIn(affected, snapshot["exposed_tools"])
        self.assertEqual(transport.calls, 0)

    async def test_future_release_executes_selected_compiled_hacs_adapter(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        payload = {
            "success": True,
            "data": {"count": 1, "results": []},
            "metadata": {"home_assistant_timezone": "UTC"},
        }
        call_result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        payload, sort_keys=True, separators=(",", ":")
                    ),
                }
            ],
            "structuredContent": payload,
            "isError": False,
        }
        transport = _GatewayTransport(
            self.capture["tools"],
            version=version,
            call_result=call_result,
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=self._registry(self._raw(entry=entry)),
        )
        server = FastMCP("signed-adapter-binding")
        snapshot = await gateway.initialize(server)
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        route = gateway._exposed["ha_get_hacs_info"]
        self.assertEqual(route.server_version, version)
        self.assertEqual(route.adapter_version, "8.2.0")
        encoded = await gateway._registered_tool_registry.snapshot()[
            "ha_get_hacs_info"
        ].run({"action": "search"})
        value = json.loads(encoded)
        self.assertTrue(value["success"], value)
        self.assertEqual(
            value["data"],
            {
                "data": {"success": True, "count": 1, "results": []},
                "metadata": {"home_assistant_timezone": "UTC"},
            },
        )
        self.assertEqual(transport.calls, 1)

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
        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn("synthetic_future_write", encoded)
        self.assertNotIn(version, encoded)

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

    async def test_verified_revocation_blocks_active_route_when_cache_fails(self):
        first = self.signer.raw(
            sequence=1,
            generated_at=self.now - timedelta(minutes=2),
            expires_at=self.now + timedelta(days=1),
            entries=[],
            revocations=[],
        )
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        signed_revocation = {
            "entry_id": self.release.entry_id,
            "server_name": "ha-mcp",
            "version": "8.2.0",
            "image_index_digest": self.release.image_index_digest,
            "revoked_at": (self.now - timedelta(minutes=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "reason": "Synthetic exact release revocation.",
        }
        second = self.signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(days=1),
            entries=[],
            revocations=[signed_revocation],
        )
        responses = [
            self.signer.journal_raw(envelopes=[first]),
            self.signer.journal_raw(envelopes=[first, second]),
        ]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=Path(self.temporary.name) / "revocation-cache.json",
            fetcher=fetcher,
            now=lambda: self.now,
        )
        transport = _GatewayTransport(
            self.capture["tools"], version="8.2.0"
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=registry,
        )
        server = FastMCP("signed-revocation-cache-failure")
        first_snapshot = await gateway.initialize(server)
        self.assertEqual(first_snapshot["dynamically_exposed_count"], 25)
        old_tool = gateway._registered_tool_registry.snapshot()[
            "ha_list_services"
        ]
        with patch.object(
            registry,
            "_write_cache",
            side_effect=ReleaseRegistryOperationalError(
                "registry_cache_write_failed"
            ),
        ):
            self.assertFalse(await registry.refresh())
        self.assertTrue(registry.authority().revoked("ha-mcp", "8.2.0"))
        result = json.loads(await old_tool.run({}))
        self.assertFalse(result["success"])
        self.assertEqual(transport.calls, 0)
        self.assertEqual(gateway._registered_tool_registry.snapshot(), {})

    async def test_revocation_source_capacity_retires_route_before_dispatch(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        chain = []
        previous = None
        for sequence in range(1, 11):
            revoked_version = version if sequence == 10 else f"8.1.{sequence}"
            raw = self.signer.raw(
                sequence=sequence,
                previous_registry_sha256=previous,
                generated_at=self.now - timedelta(minutes=11 - sequence),
                expires_at=self.now + timedelta(days=1),
                entries=[entry] if sequence == 1 else [],
                revocations=(
                    []
                    if sequence == 1
                    else [
                        revocation(
                            entry_id=(
                                entry["entry_id"]
                                if sequence == 10
                                else f"ha-mcp-{revoked_version}-synthetic"
                            ),
                            version=revoked_version,
                        )
                    ]
                ),
            )
            chain.append(raw)
            previous = RegistryEnvelope.from_bytes(raw).content_digest
        responses = [
            self.signer.journal_raw(envelopes=[chain[0]]),
            self.signer.journal_raw(envelopes=chain),
        ]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        cache = Path(self.temporary.name) / "revocation-capacity-cache.json"
        registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=fetcher,
            now=lambda: self.now,
        )
        transport = _GatewayTransport(self.capture["tools"], version=version)
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=registry,
        )
        first = await gateway.initialize(
            FastMCP("signed-revocation-capacity")
        )
        self.assertEqual(first["dynamically_exposed_count"], 25)
        old_tool = gateway._registered_tool_registry.snapshot()[
            "ha_list_services"
        ]

        with patch.object(
            registry,
            "_write_pending_barrier",
            side_effect=ReleaseRegistryOperationalError(
                "registry_cache_write_failed"
            ),
        ):
            self.assertFalse(await registry.refresh())
        self.assertTrue(registry.authority().surface_denied)
        self.assertFalse(cache.exists())
        self.assertTrue(
            cache.with_name(cache.name + ".previous").exists()
        )
        result = json.loads(await old_tool.run({}))
        self.assertFalse(result["success"])
        self.assertEqual(transport.calls, 0)
        self.assertEqual(gateway._registered_tool_registry.snapshot(), {})

        async def unavailable(_url, _maximum):
            raise OSError("synthetic registry outage")

        restarted_registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=unavailable,
            now=lambda: self.now,
        )
        self.assertTrue(restarted_registry.authority().surface_denied)
        self.assertFalse(
            restarted_registry.authority().positive_authority_current
        )
        restarted_transport = _GatewayTransport(
            self.capture["tools"],
            version=version,
        )
        restarted_gateway = UpstreamReadGateway()
        restarted_gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=restarted_transport,
            release_registry=self.compiled,
            signed_release_registry=restarted_registry,
        )
        restarted_snapshot = await restarted_gateway.initialize(
            FastMCP("signed-revocation-capacity-restart")
        )
        self.assertEqual(
            restarted_snapshot["dynamically_exposed_count"],
            0,
        )
        self.assertEqual(restarted_transport.calls, 0)

    async def test_denial_journal_overflow_never_republishes_provider_routes(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        first = self.signer.raw(
            sequence=1,
            generated_at=self.now - timedelta(minutes=2),
            expires_at=self.now + timedelta(days=1),
            entries=[entry],
            revocations=[],
        )
        previous = RegistryEnvelope.from_bytes(first).content_digest
        candidates = []
        for sequence in range(2, MAX_DENIAL_JOURNALS + 3):
            is_overflow_candidate = (
                sequence == MAX_DENIAL_JOURNALS + 2
            )
            candidate = self.signer.raw(
                sequence=sequence,
                previous_registry_sha256=previous,
                generated_at=self.now + timedelta(seconds=sequence),
                expires_at=self.now + timedelta(days=1),
                entries=[],
                revocations=[
                    revocation(
                        entry_id=(
                            entry["entry_id"]
                            if is_overflow_candidate
                            else f"ha-mcp-8.0.{sequence}-synthetic"
                        ),
                        version=(
                            version
                            if is_overflow_candidate
                            else f"8.0.{sequence}"
                        ),
                    )
                ],
            )
            candidates.append(candidate)
            previous = RegistryEnvelope.from_bytes(
                candidate
            ).content_digest
        recovery_sequence = MAX_DENIAL_JOURNALS + 3
        recovery = self.signer.raw(
            sequence=recovery_sequence,
            previous_registry_sha256=previous,
            generated_at=self.now + timedelta(seconds=recovery_sequence),
            expires_at=self.now + timedelta(days=1),
            entries=[entry],
            revocations=[
                revocation(
                    entry_id=f"ha-mcp-8.0.{sequence}-synthetic",
                    version=f"8.0.{sequence}",
                )
                for sequence in range(2, MAX_DENIAL_JOURNALS + 2)
            ],
        )
        recovery_raw = self.signer.journal_raw(envelopes=[recovery])
        responses = [
            self.signer.journal_raw(envelopes=[first]),
            *[
                self.signer.journal_raw(envelopes=[candidate])
                for candidate in candidates
            ],
            recovery_raw,
            recovery_raw,
            recovery_raw,
        ]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        cache = Path(self.temporary.name) / "denial-journal-overflow.json"
        registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=fetcher,
            now=lambda: self.now,
        )
        transport = _GatewayTransport(self.capture["tools"], version=version)
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=registry,
        )
        first_snapshot = await gateway.initialize(
            FastMCP("signed-denial-journal-overflow")
        )
        self.assertEqual(first_snapshot["dynamically_exposed_count"], 25)
        old_tool = gateway._registered_tool_registry.snapshot()[
            "ha_list_services"
        ]
        self.assertTrue(await registry.refresh())
        for _candidate in candidates[1:]:
            self.assertFalse(await registry.refresh())
        result = json.loads(await old_tool.run({}))
        self.assertFalse(result["success"])
        self.assertEqual(transport.calls, 0)
        self.assertEqual(gateway._registered_tool_registry.snapshot(), {})

        restarted_registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=fetcher,
            now=lambda: self.now,
        )
        self.assertTrue(restarted_registry.authority().surface_denied)
        self.assertFalse(await restarted_registry.refresh())
        restarted_transport = _GatewayTransport(
            self.capture["tools"],
            version=version,
        )
        restarted_gateway = UpstreamReadGateway()
        restarted_gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=restarted_transport,
            release_registry=self.compiled,
            signed_release_registry=restarted_registry,
        )
        restarted_snapshot = await restarted_gateway.initialize(
            FastMCP("signed-denial-journal-overflow-restart")
        )
        self.assertEqual(
            restarted_snapshot["dynamically_exposed_count"],
            0,
        )
        self.assertEqual(
            restarted_gateway._registered_tool_registry.snapshot(),
            {},
        )
        self.assertEqual(restarted_transport.calls, 0)

    async def test_composed_storage_outage_never_republishes_provider_routes(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        first = self.signer.raw(
            sequence=1,
            generated_at=self.now - timedelta(minutes=2),
            expires_at=self.now + timedelta(days=1),
            entries=[entry],
            revocations=[],
        )
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = self.signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=self.now + timedelta(seconds=1),
            expires_at=self.now + timedelta(days=1),
            entries=[],
            revocations=[
                revocation(
                    entry_id=entry["entry_id"],
                    version=version,
                )
            ],
        )
        outage_active = False
        fetch_count = 0

        async def fetcher(_url, _maximum):
            nonlocal fetch_count, outage_active
            fetch_count += 1
            if fetch_count == 1:
                return self.signer.journal_raw(envelopes=[first])
            outage_active = True
            return self.signer.journal_raw(envelopes=[second])

        original_replace = os.replace
        original_temporary = tempfile.NamedTemporaryFile

        def fail_storage_replace(source, target):
            if outage_active:
                raise OSError("synthetic cache volume outage")
            return original_replace(source, target)

        def fail_pending_temporary(*args, **kwargs):
            if outage_active and ".pending." in kwargs.get("prefix", ""):
                raise OSError("synthetic cache volume outage")
            return original_temporary(*args, **kwargs)

        cache = Path(self.temporary.name) / "composed-storage-outage.json"
        registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=fetcher,
            now=lambda: self.now,
        )
        transport = _GatewayTransport(self.capture["tools"], version=version)
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=registry,
        )
        snapshot = await gateway.initialize(
            FastMCP("signed-composed-storage-outage")
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        old_tool = gateway._registered_tool_registry.snapshot()[
            "ha_list_services"
        ]
        with patch(
            "ha_mcp_engineering.ha_mcp_readmission.registry.os.replace",
            side_effect=fail_storage_replace,
        ), patch(
            "ha_mcp_engineering.ha_mcp_readmission.registry."
            "tempfile.NamedTemporaryFile",
            side_effect=fail_pending_temporary,
        ):
            self.assertFalse(await registry.refresh())
        denied = json.loads(await old_tool.run({}))
        self.assertFalse(denied["success"])
        self.assertEqual(transport.calls, 0)

        stale = self.signer.journal_raw(envelopes=[first])

        async def stale_fetcher(_url, _maximum):
            return stale

        restarted_registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=stale_fetcher,
            now=lambda: self.now,
        )
        restarted_transport = _GatewayTransport(
            self.capture["tools"],
            version=version,
        )
        restarted_gateway = UpstreamReadGateway()
        restarted_gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=restarted_transport,
            release_registry=self.compiled,
            signed_release_registry=restarted_registry,
        )
        restarted_snapshot = await restarted_gateway.initialize(
            FastMCP("signed-composed-storage-outage-restart")
        )
        self.assertEqual(
            restarted_snapshot["dynamically_exposed_count"],
            0,
        )
        self.assertEqual(
            restarted_gateway._registered_tool_registry.snapshot(),
            {},
        )
        self.assertEqual(restarted_transport.calls, 0)

    async def test_bootstrap_revocation_failure_never_publishes_provider_routes(self):
        signed_revocation = {
            "entry_id": self.release.entry_id,
            "server_name": "ha-mcp",
            "version": "8.2.0",
            "image_index_digest": self.release.image_index_digest,
            "revoked_at": (self.now - timedelta(minutes=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "reason": "Synthetic exact release revocation.",
        }
        raw = self._raw(entry=None, revocations=[signed_revocation])
        fetched = False

        async def fetcher(_url, _maximum):
            nonlocal fetched
            fetched = True
            return raw

        original_temporary = tempfile.NamedTemporaryFile

        def fail_candidate_storage(*args, **kwargs):
            if fetched and ".pending." in kwargs.get("prefix", ""):
                raise OSError("synthetic cache volume outage")
            return original_temporary(*args, **kwargs)

        cache = Path(self.temporary.name) / "bootstrap-denial.json"
        registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=fetcher,
            now=lambda: self.now,
        )
        transport = _GatewayTransport(self.capture["tools"], version="8.2.0")
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=registry,
        )
        with patch(
            "ha_mcp_engineering.ha_mcp_readmission.registry."
            "tempfile.NamedTemporaryFile",
            side_effect=fail_candidate_storage,
        ):
            snapshot = await gateway.initialize(
                FastMCP("signed-bootstrap-denial")
            )
        self.assertEqual(snapshot["dynamically_exposed_count"], 0)
        self.assertEqual(gateway._registered_tool_registry.snapshot(), {})
        self.assertEqual(transport.calls, 0)

        async def unavailable(_url, _maximum):
            raise OSError("synthetic registry outage")

        restarted_registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=unavailable,
            now=lambda: self.now,
        )
        restarted_transport = _GatewayTransport(
            self.capture["tools"], version="8.2.0"
        )
        restarted_gateway = UpstreamReadGateway()
        restarted_gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=restarted_transport,
            release_registry=self.compiled,
            signed_release_registry=restarted_registry,
        )
        restarted_snapshot = await restarted_gateway.initialize(
            FastMCP("signed-bootstrap-denial-restart")
        )
        self.assertEqual(
            restarted_snapshot["dynamically_exposed_count"], 0
        )
        self.assertEqual(
            restarted_gateway._registered_tool_registry.snapshot(), {}
        )
        self.assertEqual(restarted_transport.calls, 0)

    async def test_missing_signed_cache_never_republishes_revoked_routes(self):
        signed_revocation = {
            "entry_id": self.release.entry_id,
            "server_name": "ha-mcp",
            "version": "8.2.0",
            "image_index_digest": self.release.image_index_digest,
            "revoked_at": (self.now - timedelta(minutes=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "reason": "Synthetic exact release revocation.",
        }
        raw = self._raw(entry=None, revocations=[signed_revocation])
        cache = Path(self.temporary.name) / "missing-main-cache.json"

        async def fetcher(_url, _maximum):
            return raw

        registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=fetcher,
            now=lambda: self.now,
        )
        self.assertTrue(await registry.refresh())
        self.assertTrue(cache.exists())
        cache.unlink()

        async def unavailable(_url, _maximum):
            raise OSError("synthetic registry outage")

        restarted_registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=unavailable,
            now=lambda: self.now,
        )
        transport = _GatewayTransport(self.capture["tools"], version="8.2.0")
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=restarted_registry,
        )
        snapshot = await gateway.initialize(
            FastMCP("signed-missing-main-cache")
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 0)
        self.assertEqual(gateway._registered_tool_registry.snapshot(), {})
        self.assertEqual(transport.calls, 0)

    async def test_initial_registry_outage_preserves_compiled_exact_routes(self):
        async def unavailable(_url, _maximum):
            raise OSError("synthetic registry outage")

        cache = Path(self.temporary.name) / "initial-outage.json"
        registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=unavailable,
            now=lambda: self.now,
        )
        transport = _GatewayTransport(self.capture["tools"], version="8.2.0")
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=registry,
        )
        snapshot = await gateway.initialize(
            FastMCP("signed-initial-outage")
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        self.assertFalse(registry.authority().surface_denied)
        self.assertTrue(registry._lifecycle_witness_path().exists())
        self.assertEqual(transport.calls, 0)

    async def test_stale_pending_never_republishes_provider_routes(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        first = self.signer.raw(
            sequence=1,
            generated_at=self.now - timedelta(minutes=2),
            expires_at=self.now + timedelta(days=1),
            entries=[entry],
            revocations=[],
        )
        first_digest = RegistryEnvelope.from_bytes(first).content_digest
        second = self.signer.raw(
            sequence=2,
            previous_registry_sha256=first_digest,
            generated_at=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(days=1),
            entries=[entry],
            revocations=[],
        )
        second_digest = RegistryEnvelope.from_bytes(second).content_digest
        third = self.signer.raw(
            sequence=3,
            previous_registry_sha256=second_digest,
            generated_at=self.now + timedelta(seconds=1),
            expires_at=self.now + timedelta(days=1),
            entries=[],
            revocations=[
                revocation(
                    entry_id=entry["entry_id"],
                    version=version,
                )
            ],
        )
        responses = [
            self.signer.journal_raw(envelopes=[first]),
            self.signer.journal_raw(envelopes=[second]),
            self.signer.journal_raw(envelopes=[third]),
        ]

        async def fetcher(_url, _maximum):
            return responses.pop(0)

        cache = Path(self.temporary.name) / "stale-pending.json"
        pending = cache.with_name(cache.name + ".pending")
        registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=fetcher,
            now=lambda: self.now,
        )
        self.assertTrue(await registry.refresh())
        original_unlink = Path.unlink

        def retain_pending(path, *args, **kwargs):
            if path == pending:
                raise OSError("synthetic pending cleanup failure")
            return original_unlink(path, *args, **kwargs)

        with patch.object(
            Path,
            "unlink",
            autospec=True,
            side_effect=retain_pending,
        ):
            self.assertTrue(await registry.refresh())
        original_replace = os.replace

        def reject_pending_replacement(source, target):
            if Path(target) == pending:
                raise OSError("synthetic pending replacement failure")
            return original_replace(source, target)

        with patch(
            "ha_mcp_engineering.ha_mcp_readmission.registry.os.replace",
            side_effect=reject_pending_replacement,
        ):
            self.assertFalse(await registry.refresh())

        alternate = self.signer.raw(
            sequence=3,
            previous_registry_sha256=second_digest,
            generated_at=self.now + timedelta(seconds=1),
            expires_at=self.now + timedelta(days=1),
            entries=[entry],
            revocations=[],
        )
        alternate_raw = self.signer.journal_raw(envelopes=[alternate])

        async def alternate_fetcher(_url, _maximum):
            return alternate_raw

        restarted_registry = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=alternate_fetcher,
            now=lambda: self.now,
        )
        restarted_transport = _GatewayTransport(
            self.capture["tools"],
            version=version,
        )
        restarted_gateway = UpstreamReadGateway()
        restarted_gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=restarted_transport,
            release_registry=self.compiled,
            signed_release_registry=restarted_registry,
        )
        restarted_snapshot = await restarted_gateway.initialize(
            FastMCP("signed-stale-pending-restart")
        )
        self.assertEqual(
            restarted_snapshot["dynamically_exposed_count"],
            0,
        )
        self.assertEqual(
            restarted_gateway._registered_tool_registry.snapshot(),
            {},
        )
        self.assertEqual(restarted_transport.calls, 0)

    async def test_committed_main_accepts_only_exact_pending_cleanup_residue(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        stale = self.signer.raw(
            sequence=1,
            generated_at=self.now - timedelta(minutes=2),
            expires_at=self.now + timedelta(days=1),
            entries=[],
            revocations=[
                revocation(
                    entry_id=entry["entry_id"],
                    version=version,
                )
            ],
        )
        stale_digest = RegistryEnvelope.from_bytes(stale).content_digest
        committed = self.signer.raw(
            sequence=2,
            previous_registry_sha256=stale_digest,
            generated_at=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(days=1),
            entries=[entry],
            revocations=[],
        )
        equal_conflict = self.signer.raw(
            sequence=2,
            previous_registry_sha256=stale_digest,
            generated_at=self.now - timedelta(minutes=1),
            expires_at=self.now + timedelta(days=1),
            entries=[],
            revocations=[],
        )
        disconnected = self.signer.raw(
            sequence=3,
            previous_registry_sha256="sha256:" + "f" * 64,
            generated_at=self.now,
            expires_at=self.now + timedelta(days=1),
            entries=[entry],
            revocations=[],
        )

        def pending_document(
            envelope: bytes,
            *,
            retained: tuple[bytes, ...] = (),
        ) -> bytes:
            return canonical_json(
                {
                    "schema_version": PENDING_SCHEMA_VERSION,
                    "candidate_journal": json.loads(
                        self.signer.journal_raw(envelopes=[envelope])
                    ),
                    "retained_denial_journals": [
                        json.loads(
                            self.signer.journal_raw(envelopes=[item])
                        )
                        for item in retained
                    ],
                }
            )

        cases = {
            "malformed": b"not-json",
            "oversized": b"x" * (MAX_CACHE_BYTES + 1),
            "lower_revocation": pending_document(stale),
            "equal_conflict": pending_document(equal_conflict),
            "disconnected": pending_document(disconnected),
            "uncommitted_retained_denial": pending_document(
                committed,
                retained=(stale,),
            ),
        }

        async def unavailable(_url, _maximum):
            raise OSError("synthetic registry outage")

        for label, pending_payload in cases.items():
            with self.subTest(label=label):
                cache = Path(self.temporary.name) / f"pending-{label}.json"

                async def fetcher(_url, _maximum):
                    return self.signer.journal_raw(
                        envelopes=[committed]
                    )

                writer = SignedReleaseRegistry(
                    enabled=True,
                    public_key=self.signer.public_key_base64,
                    cache_path=cache,
                    fetcher=fetcher,
                    now=lambda: self.now,
                )
                self.assertTrue(await writer.refresh())
                cache.with_name(cache.name + ".pending").write_bytes(
                    pending_payload
                )
                restarted = SignedReleaseRegistry(
                    enabled=True,
                    public_key=self.signer.public_key_base64,
                    cache_path=cache,
                    fetcher=unavailable,
                    now=lambda: self.now,
                )
                transport = _GatewayTransport(
                    self.capture["tools"], version=version
                )
                gateway = UpstreamReadGateway()
                gateway.configure(
                    _settings(self.signer.public_key_base64),
                    transport=transport,
                    release_registry=self.compiled,
                    signed_release_registry=restarted,
                )
                snapshot = await gateway.initialize(
                    FastMCP(f"signed-pending-{label}")
                )
                self.assertTrue(restarted.authority().surface_denied)
                self.assertEqual(
                    snapshot["dynamically_exposed_count"], 0
                )
                self.assertEqual(
                    gateway._registered_tool_registry.snapshot(), {}
                )
                self.assertEqual(transport.calls, 0)

        cache = Path(self.temporary.name) / "pending-exact-cleanup.json"

        async def fetcher(_url, _maximum):
            return self.signer.journal_raw(envelopes=[committed])

        writer = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=fetcher,
            now=lambda: self.now,
        )
        self.assertTrue(await writer.refresh())
        writer._write_pending_barrier(
            writer._authority_journal,
            parent=cache.parent,
            denial_journals=(writer._authority_journal,),
        )
        restarted = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=unavailable,
            now=lambda: self.now,
        )
        transport = _GatewayTransport(self.capture["tools"], version=version)
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=restarted,
        )
        snapshot = await gateway.initialize(
            FastMCP("signed-pending-exact-cleanup")
        )
        self.assertFalse(restarted.authority().surface_denied)
        self.assertEqual(snapshot["dynamically_exposed_count"], 25)
        self.assertEqual(
            len(gateway._registered_tool_registry.snapshot()), 25
        )
        self.assertEqual(transport.calls, 0)

    async def test_corrupt_retained_cache_denies_compiled_exact_on_restart(self):
        signed_revocation = {
            "entry_id": self.release.entry_id,
            "server_name": "ha-mcp",
            "version": "8.2.0",
            "image_index_digest": self.release.image_index_digest,
            "revoked_at": (self.now - timedelta(minutes=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "reason": "Synthetic exact release revocation.",
        }
        cache = Path(self.temporary.name) / "corrupt-revocation-cache.json"
        valid = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=lambda _url, _maximum: None,
            now=lambda: self.now,
        )

        async def valid_fetcher(_url, _maximum):
            return self._raw(entry=None, revocations=[signed_revocation])

        valid._fetcher = valid_fetcher
        self.assertTrue(await valid.refresh())
        cache.write_bytes(b'{"schema_version":2,"accepted_registry":')

        async def unavailable(_url, _maximum):
            raise OSError("synthetic registry outage")

        restarted = SignedReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=cache,
            fetcher=unavailable,
            now=lambda: self.now,
        )
        transport = _GatewayTransport(
            self.capture["tools"], version="8.2.0"
        )
        gateway = UpstreamReadGateway()
        gateway.configure(
            _settings(self.signer.public_key_base64),
            transport=transport,
            release_registry=self.compiled,
            signed_release_registry=restarted,
        )
        snapshot = await gateway.initialize(
            FastMCP("corrupt-cache-denies-compiled-exact")
        )
        self.assertEqual(snapshot["dynamically_exposed_count"], 0)
        self.assertTrue(
            snapshot["automatic_readmission_registry"]["surface_denied"]
        )
        self.assertEqual(
            snapshot["last_discovery_failure_category"],
            "upstream_version_mismatch",
        )
        self.assertNotEqual(
            snapshot["last_discovery_failure_category"],
            "internal_error",
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
                encoded = json.dumps(snapshot, sort_keys=True)
                if server_name != "ha-mcp":
                    self.assertNotIn(server_name, encoded)
                self.assertNotIn(version, encoded)
                self.assertNotIn(protocol_version, encoded)

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

    async def test_session_rotation_retires_generation_before_dispatch(self):
        version = "8.2.1"
        entry = _signed_entry_for(self.release, version=version)
        gateway, transport, snapshot = await self._initialize(
            raw=self._raw(entry=entry),
            version=version,
        )
        original_generation = snapshot["readmission_generation"]
        tool = gateway._registered_tool_registry.snapshot()[
            "ha_list_services"
        ]
        transport.catalog = replace(
            transport.catalog,
            session_id="synthetic-ha-mcp-session-rotated",
        )
        result = json.loads(await tool.run({}))
        self.assertFalse(result["success"])
        self.assertEqual(transport.calls, 0)
        self.assertEqual(gateway._registered_tool_registry.snapshot(), {})
        lifecycle = gateway.health_snapshot()["automatic_readmission"]
        self.assertIsNone(
            gateway._readmission_coordinator.generation_for(
                UpstreamSurface.HA_MCP
            )
        )
        self.assertIsNotNone(original_generation)
        self.assertEqual(lifecycle["issued_lease_count"], 0)

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
            {
                "health": snapshot,
                "automatic": automatic,
                "registry": registry,
                "audit": audit,
            },
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
        self.assertNotIn(version, encoded)
        self.assertEqual(snapshot["reviewed_supported_versions"], [])
        self.assertEqual(
            snapshot["reviewed_supported_version_count"],
            len(self.compiled.by_version),
        )
        for compiled_version in self.compiled.by_version:
            self.assertNotIn(compiled_version, encoded)
        self.assertNotIn(entry["source_commit"], encoded)
        self.assertNotIn(entry["image_index_digest"], encoded)
        for digest in entry["architecture_image_digests"].values():
            self.assertNotIn(digest, encoded)
        self.assertLessEqual(len(audit), 8)

        tool = gateway._registered_tool_registry.snapshot()[
            "ha_list_services"
        ]
        telemetry, token = begin_request("signed-release-audit-redaction")
        try:
            result = json.loads(await tool.run({}))
        finally:
            end_request(token)
        self.assertTrue(result["success"])
        self.assertEqual(
            telemetry.audit_context["upstream_version_evidence"],
            "signed_registry",
        )
        self.assertNotIn(
            version,
            json.dumps(telemetry.audit_context, sort_keys=True),
        )

        compiled_gateway, _compiled_transport, _compiled_snapshot = (
            await self._initialize(
                raw=self._raw(entry=None),
                version="8.2.0",
            )
        )
        compiled_tool = compiled_gateway._registered_tool_registry.snapshot()[
            "ha_list_services"
        ]
        compiled_telemetry, compiled_token = begin_request(
            "compiled-release-audit-redaction"
        )
        try:
            compiled_result = json.loads(await compiled_tool.run({}))
        finally:
            end_request(compiled_token)
        self.assertTrue(compiled_result["success"])
        self.assertEqual(
            compiled_telemetry.audit_context["upstream_version_evidence"],
            "compiled_exact",
        )
        self.assertNotIn(
            "8.2.0",
            json.dumps(compiled_telemetry.audit_context, sort_keys=True),
        )


if __name__ == "__main__":
    unittest.main()
