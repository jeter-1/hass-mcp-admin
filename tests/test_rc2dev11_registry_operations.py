import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.providers.upstream_contracts import (  # noqa: E402
    COMPILED_CONTRACT_FAMILIES,
    CONTRACT_FAMILY,
    ContractValidationError,
    ReleaseAttestation,
    canonical_json,
    decide_admission,
    load_attestations,
    normalize_runtime_contract,
)
from ha_mcp_engineering.clients.mcp import McpDashboardHandshake  # noqa: E402
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import DashboardProviderError, ErrorCode  # noqa: E402
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
)
from ha_mcp_engineering.providers.upstream_registry import (  # noqa: E402
    CACHE_HARD_AGE_SECONDS,
    REGISTRY_URL,
    SIGNATURE_URL,
    UpstreamTrustRegistry,
)
from scripts.manage_upstream_trust_registry import (  # noqa: E402
    RegistryOperationError,
    RegistryPaths,
    mutate_registry,
    signing_material,
    verify_committed_registry,
)


CONTRACT_PATH = (
    BETA
    / "ha_mcp_engineering"
    / "providers"
    / "contracts"
    / "ha_mcp_7_14_dashboard_read_v2.json"
)


class RegistryFixture:
    def __init__(self, directory: str):
        root = Path(directory)
        self.paths = RegistryPaths(
            registry=root / "upstream-trust" / "upstream-dashboard-registry.json",
            signature=root
            / "upstream-trust"
            / "upstream-dashboard-registry.sig.json",
            evidence_directory=root / "docs" / "evidence" / "upstream-compatibility",
            index=root / "docs" / "generated" / "UPSTREAM_TRUST_REGISTRY_INDEX.md",
            runtime_evidence=root / ".compat" / "runtime-evidence.json",
            release_evidence=root / ".compat" / "release-evidence.json",
        )
        self.private = Ed25519PrivateKey.generate()
        private_raw = self.private.private_bytes_raw()
        public_raw = self.private.public_key().public_bytes_raw()
        self.environment = {
            "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY": base64.b64encode(private_raw).decode(),
            "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY": base64.b64encode(public_raw).decode(),
            "UPSTREAM_TRUST_REGISTRY_KEY_ID": "test-only-registry-key-v1",
        }

    def evidence(self, version: str) -> None:
        tool = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        contract = normalize_runtime_contract(tool, protocol_version="2025-03-26")
        runtime = {
            "server_name": "ha-mcp",
            "server_version": version,
            "protocol_version": "2025-03-26",
            "required_tool": tool,
            "contract_fingerprints": {
                "input": contract.input_fingerprint,
                "security": contract.security_fingerprint,
                "output": contract.output_fingerprint,
                "runtime": contract.runtime_fingerprint,
            },
            "informational_fingerprints": {
                "raw_input_schema": "1" * 64,
                "reviewed_security_descriptor": "2" * 64,
                "fixture_runtime_descriptor": "3" * 64,
                "published_runtime_descriptor": "4" * 64,
            },
            "catalog_fingerprint": "5" * 64,
            "write_dispatches": 0,
            "negative_reachability": {
                "rejected_before_dispatch": [
                    "ha_set_entity",
                    "ha_set_device",
                    "ha_call_service",
                    "ha_bulk_control",
                    "ha_config_set_dashboard",
                    "ha_config_delete_dashboard",
                ],
                "include_screenshot_true_rejected": True,
                "generic_forwarder_present": False,
            },
        }
        release = {
            "version": version,
            "source_tag": f"v{version}",
            "source_commit": "a" * 40,
            "image_index_digest": "sha256:" + "b" * 64,
            "image_revision": "c" * 40,
            "image_created": "2026-07-20T00:00:00Z",
            "image_source": "https://github.com/homeassistant-ai/ha-mcp",
            "dirty_label": "false",
            "platform_digests": {
                "linux/amd64": "sha256:" + "d" * 64,
                "linux/arm64": "sha256:" + "e" * 64,
                "linux/arm/v7": "sha256:" + "f" * 64,
            },
            "slsa_provenance": "present_per_platform",
            "sbom": "present",
            "official_repository": "homeassistant-ai/ha-mcp",
            "official_image": "ghcr.io/homeassistant-ai/ha-mcp",
        }
        self.paths.runtime_evidence.parent.mkdir(parents=True, exist_ok=True)
        self.paths.runtime_evidence.write_bytes(canonical_json(runtime))
        self.paths.release_evidence.write_bytes(canonical_json(release))

    def mutate(self, operation: str, sequence: int, version: str | None = None, **kwargs):
        return mutate_registry(
            operation=operation,
            upstream_version=version,
            expected_current_sequence=sequence,
            paths=self.paths,
            environment=self.environment,
            now=kwargs.pop("now", datetime(2026, 7, 20, tzinfo=timezone.utc)),
            **kwargs,
        )


class RegistryLifecycleCliTests(unittest.TestCase):
    def test_every_mutation_supports_a_verified_no_write_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2")

            def hashes():
                return {
                    path: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in Path(directory).rglob("*")
                    if path.is_file()
                }

            fixture.evidence("7.14.3")
            before = hashes()
            for operation, version in (
                ("add", "7.14.3"),
                ("revoke", "7.14.2"),
                ("renew", None),
            ):
                summary = fixture.mutate(
                    operation, 1, version, dry_run=True
                )
                self.assertTrue(summary["dry_run"])
                self.assertEqual(before, hashes())
            fixture.mutate("revoke", 1, "7.14.2")
            before_restore = hashes()
            summary = fixture.mutate("restore", 2, "7.14.2", dry_run=True)
            self.assertTrue(summary["dry_run"])
            self.assertEqual(before_restore, hashes())

    def test_bootstrap_dry_run_and_public_only_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            evidence_files = {
                path for path in Path(directory).rglob("*") if path.is_file()
            }
            summary = fixture.mutate("bootstrap", 0, "7.14.2", dry_run=True)
            self.assertEqual(summary["new_sequence"], 1)
            self.assertFalse(summary["written"])
            self.assertFalse(fixture.paths.registry.exists())
            summary = fixture.mutate("bootstrap", 0, "7.14.2")
            self.assertEqual(summary["new_sequence"], 1)
            self.assertEqual(
                {
                    fixture.paths.registry,
                    fixture.paths.signature,
                    fixture.paths.evidence_directory / "ha-mcp-7.14.2.json",
                    fixture.paths.index,
                },
                {
                    path for path in Path(directory).rglob("*") if path.is_file()
                }
                - evidence_files,
            )
            public_only = {
                "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY": fixture.environment[
                    "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY"
                ]
            }
            verified = verify_committed_registry(
                paths=fixture.paths,
                environment=public_only,
                now=datetime(2026, 7, 21, tzinfo=timezone.utc),
            )
            self.assertTrue(verified["signature_valid"])
            self.assertEqual(verified["sequence"], 1)
            canonical_registry = fixture.paths.registry.read_bytes()
            fixture.paths.registry.write_text(
                json.dumps(json.loads(canonical_registry), indent=2), encoding="utf-8"
            )
            with self.assertRaisesRegex(RegistryOperationError, "canonical JSON"):
                verify_committed_registry(
                    paths=fixture.paths,
                    environment=public_only,
                    now=datetime(2026, 7, 21, tzinfo=timezone.utc),
                )
            fixture.paths.registry.write_bytes(canonical_registry)
            with self.assertRaisesRegex(RegistryOperationError, "bootstrap requires"):
                fixture.mutate("bootstrap", 0, "7.14.2")

    def test_add_revoke_restore_and_renew_each_advance_once(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2")
            fixture.evidence("7.14.3")
            added = fixture.mutate("add", 1, "7.14.3")
            self.assertEqual(added["new_sequence"], 2)
            with self.assertRaisesRegex(RegistryOperationError, "duplicate"):
                fixture.mutate("add", 2, "7.14.3")
            revoked = fixture.mutate("revoke", 2, "7.14.3")
            self.assertEqual((revoked["old_revoked"], revoked["new_revoked"]), (False, True))
            restored = fixture.mutate("restore", 3, "7.14.3")
            self.assertEqual((restored["old_revoked"], restored["new_revoked"]), (True, False))
            before = json.loads(fixture.paths.registry.read_text())["entries"]
            renewed = fixture.mutate(
                "renew",
                4,
                now=datetime(2026, 8, 1, tzinfo=timezone.utc),
            )
            after_value = json.loads(fixture.paths.registry.read_text())
            self.assertEqual(renewed["new_sequence"], 5)
            self.assertEqual(before, after_value["entries"])
            self.assertEqual(after_value["expires_at"], "2026-10-30T00:00:00Z")
            verified = verify_committed_registry(
                paths=fixture.paths,
                environment={
                    "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY": fixture.environment[
                        "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY"
                    ]
                },
                now=datetime(2026, 8, 2, tzinfo=timezone.utc),
            )
            self.assertTrue(verified["generated_evidence_valid"])

    def test_stale_sequence_and_key_errors_change_no_files_or_output_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2")
            before = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in Path(directory).rglob("*")
                if path.is_file()
            }
            with self.assertRaisesRegex(RegistryOperationError, "stale"):
                fixture.mutate("renew", 0)
            after = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in Path(directory).rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)
            mismatched = dict(fixture.environment)
            mismatched["UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY"] = base64.b64encode(
                Ed25519PrivateKey.generate().public_key().public_bytes_raw()
            ).decode()
            with self.assertRaisesRegex(RegistryOperationError, "does not match") as context:
                signing_material(mismatched)
            self.assertNotIn(
                fixture.environment["UPSTREAM_TRUST_REGISTRY_SIGNING_KEY"],
                str(context.exception),
            )
            for name in (
                "UPSTREAM_TRUST_REGISTRY_SIGNING_KEY",
                "UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY",
            ):
                malformed = dict(fixture.environment)
                malformed[name] = "not-base64!"
                with self.assertRaises(RegistryOperationError):
                    signing_material(malformed)
                short = dict(fixture.environment)
                short[name] = base64.b64encode(b"short").decode()
                with self.assertRaises(RegistryOperationError):
                    signing_material(short)


class ReviewedAtValidationTests(unittest.TestCase):
    def setUp(self):
        self.value = copy.deepcopy(load_attestations()[0].__dict__)
        self.value["platform_digests"] = dict(self.value["platform_digests"])

    def test_explicit_utc_forms_are_accepted_and_canonicalized(self):
        self.value["reviewed_at"] = "2026-07-20T01:02:03Z"
        self.assertEqual(
            ReleaseAttestation.from_mapping(self.value).reviewed_at,
            "2026-07-20T01:02:03Z",
        )
        self.value["reviewed_at"] = "2026-07-20T01:02:03+00:00"
        self.assertEqual(
            ReleaseAttestation.from_mapping(self.value).reviewed_at,
            "2026-07-20T01:02:03Z",
        )

    def test_non_utc_naive_malformed_and_invalid_dates_reject(self):
        for value in (
            "2026-07-20T01:02:03-05:00",
            "2026-07-20T01:02:03",
            "not-a-date",
            "2026-02-30T01:02:03Z",
        ):
            with self.subTest(value=value):
                self.value["reviewed_at"] = value
                with self.assertRaisesRegex(
                    ContractValidationError, "attestation_reviewed_at_invalid"
                ):
                    ReleaseAttestation.from_mapping(self.value)


class DisposableRegistryAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def test_signed_data_cannot_define_runtime_authority(self):
        self.assertEqual(set(COMPILED_CONTRACT_FAMILIES), {CONTRACT_FAMILY})
        entry = copy.deepcopy(load_attestations()[-1].__dict__)
        entry["platform_digests"] = dict(entry["platform_digests"])
        entry["contract_family"] = "signed_data_must_not_compile_this"
        with self.assertRaisesRegex(ContractValidationError, "family_unknown"):
            ReleaseAttestation.from_mapping(entry)
        entry["contract_family"] = CONTRACT_FAMILY
        entry["tools"] = ["ha_set_entity", "ha_set_device", "ha_call_service"]
        with self.assertRaisesRegex(ContractValidationError, "fields_invalid"):
            ReleaseAttestation.from_mapping(entry)

    async def test_registry_expiry_precedes_cache_hard_age(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2", expiry_days=1)
            payload = fixture.paths.registry.read_bytes()
            signature = fixture.paths.signature.read_bytes()
            clock = {"now": datetime(2026, 7, 20, tzinfo=timezone.utc)}

            async def fetch(url, maximum):
                return signature if url == SIGNATURE_URL else payload

            manager = UpstreamTrustRegistry(
                enabled=True,
                public_key=fixture.environment["UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY"],
                cache_path=Path(directory) / "data" / "cache.json",
                fetcher=fetch,
                now=lambda: clock["now"],
            )
            self.assertTrue(await manager.refresh())
            clock["now"] += timedelta(days=2)
            self.assertLess(2 * 86_400, CACHE_HARD_AGE_SECONDS)
            self.assertFalse(manager.has_exact_attestation("ha-mcp", "7.14.2"))

    async def test_revoked_release_blocks_dashboard_tool_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2")
            fixture.mutate("revoke", 1, "7.14.2")
            payload = fixture.paths.registry.read_bytes()
            signature = fixture.paths.signature.read_bytes()

            async def fetch(url, maximum):
                return signature if url == SIGNATURE_URL else payload

            manager = UpstreamTrustRegistry(
                enabled=True,
                public_key=fixture.environment["UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY"],
                cache_path=Path(directory) / "data" / "cache.json",
                fetcher=fetch,
                now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
            )
            self.assertTrue(await manager.refresh())

            class Transport:
                dispatched = 0

                async def discover(self):
                    return McpDashboardHandshake(
                        protocol_version="2025-03-26",
                        server_name="ha-mcp",
                        server_version="7.14.2",
                        tools=(json.loads(CONTRACT_PATH.read_text()),),
                        connection_latency_ms=1.0,
                    )

                async def execute_dashboard_read(self, arguments, validator):
                    validator(await self.discover())
                    self.dispatched += 1
                    raise AssertionError("revoked dashboard dispatch reached transport")

            transport = Transport()
            provider = UpstreamDashboardProvider()
            provider.configure(
                Settings(
                    ha_url="http://supervisor/core",
                    ha_token="synthetic-test-token",
                    access_secret="synthetic-test-secret",
                    port=8100,
                    audit_path="audit.jsonl",
                    rate_limit_per_minute=120,
                    rate_limit_burst=25,
                    destructive_services=frozenset(),
                    upstream_dashboard_mcp_url="http://upstream:9583/test-secret/mcp",
                ),
                transport=transport,
                registry=manager,
            )
            with self.assertRaises(DashboardProviderError) as context:
                await provider.list_dashboards(limit=5, response_limit=60_000)
            self.assertEqual(
                context.exception.code,
                ErrorCode.UPSTREAM_DASHBOARD_UNSUPPORTED_TRUST_PROFILE,
            )
            self.assertEqual(transport.dispatched, 0)

    async def test_cache_hard_age_boundary_and_expiry_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2")
            payload = fixture.paths.registry.read_bytes()
            signature = fixture.paths.signature.read_bytes()
            clock = {"now": datetime(2026, 7, 20, tzinfo=timezone.utc)}

            async def fetch(url, maximum):
                return signature if url == SIGNATURE_URL else payload

            manager = UpstreamTrustRegistry(
                enabled=True,
                public_key=fixture.environment["UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY"],
                cache_path=Path(directory) / "data" / "cache.json",
                fetcher=fetch,
                now=lambda: clock["now"],
            )
            self.assertTrue(await manager.refresh())
            for age, usable in (
                (CACHE_HARD_AGE_SECONDS - 0.001, True),
                (CACHE_HARD_AGE_SECONDS, True),
                (CACHE_HARD_AGE_SECONDS + 0.001, False),
            ):
                clock["now"] = datetime(2026, 7, 20, tzinfo=timezone.utc) + timedelta(
                    seconds=age
                )
                self.assertEqual(
                    manager.has_exact_attestation("ha-mcp", "7.14.2"), usable
                )
            clock["now"] = datetime(2026, 10, 19, 0, 0, 1, tzinfo=timezone.utc)
            self.assertFalse(manager.has_exact_attestation("ha-mcp", "7.14.2"))

    async def test_lkg_bad_signature_rollback_revocation_restore_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(directory)
            fixture.evidence("7.14.2")
            fixture.mutate("bootstrap", 0, "7.14.2")
            pairs = [(fixture.paths.registry.read_bytes(), fixture.paths.signature.read_bytes())]
            current = {"pair": pairs[0], "unavailable": False}
            clock = {"now": datetime(2026, 7, 20, tzinfo=timezone.utc)}

            async def fetch(url, maximum):
                if current["unavailable"]:
                    raise OSError("synthetic registry outage")
                return current["pair"][1 if url == SIGNATURE_URL else 0]

            cache = Path(directory) / "data" / "cache.json"
            manager = UpstreamTrustRegistry(
                enabled=True,
                public_key=fixture.environment["UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY"],
                cache_path=cache,
                fetcher=fetch,
                now=lambda: clock["now"],
            )
            self.assertTrue(await manager.refresh())
            self.assertTrue(manager.has_exact_attestation("ha-mcp", "7.14.2"))
            reconstructed = UpstreamTrustRegistry(
                enabled=True,
                public_key=fixture.environment["UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY"],
                cache_path=cache,
                fetcher=fetch,
                now=lambda: clock["now"],
            )
            self.assertTrue(reconstructed.has_exact_attestation("ha-mcp", "7.14.2"))
            current["unavailable"] = True
            self.assertFalse(await reconstructed.refresh())
            self.assertTrue(reconstructed.has_exact_attestation("ha-mcp", "7.14.2"))
            current["unavailable"] = False
            conflicting_registry = json.loads(pairs[0][0])
            conflicting_registry["expires_at"] = "2026-10-19T00:00:00Z"
            conflicting_signature = {
                "schema_version": 1,
                "algorithm": "Ed25519",
                "key_id": conflicting_registry["key_id"],
                "signature": base64.b64encode(
                    fixture.private.sign(canonical_json(conflicting_registry))
                ).decode(),
            }
            current["pair"] = (
                canonical_json(conflicting_registry),
                canonical_json(conflicting_signature),
            )
            self.assertFalse(await reconstructed.refresh())
            self.assertEqual(
                reconstructed.snapshot()["last_registry_failure_category"],
                "upstream_registry_replay_conflict",
            )
            current["pair"] = pairs[0]
            self.assertTrue(await reconstructed.refresh())
            current["pair"] = (pairs[0][0], b'{"bad":"signature"}')
            self.assertFalse(await reconstructed.refresh())
            self.assertTrue(reconstructed.has_exact_attestation("ha-mcp", "7.14.2"))

            fixture.mutate("revoke", 1, "7.14.2", now=clock["now"] + timedelta(days=1))
            clock["now"] += timedelta(days=1)
            revoked_pair = (
                fixture.paths.registry.read_bytes(),
                fixture.paths.signature.read_bytes(),
            )
            current["pair"] = revoked_pair
            self.assertTrue(await reconstructed.refresh())
            decision = decide_admission(
                server_name="ha-mcp",
                server_version="7.14.2",
                protocol_version="2025-03-26",
                tool=json.loads(CONTRACT_PATH.read_text()),
                attestations=reconstructed.effective_attestations(),
            )
            self.assertFalse(decision.accepted)
            self.assertEqual(decision.status, "rejected_revoked_attestation")
            fixture.mutate("restore", 2, "7.14.2", now=clock["now"] + timedelta(days=2))
            clock["now"] += timedelta(days=2)
            current["pair"] = (
                fixture.paths.registry.read_bytes(),
                fixture.paths.signature.read_bytes(),
            )
            self.assertTrue(await reconstructed.refresh())
            restored = decide_admission(
                server_name="ha-mcp",
                server_version="7.14.2",
                protocol_version="2025-03-26",
                tool=json.loads(CONTRACT_PATH.read_text()),
                attestations=reconstructed.effective_attestations(),
            )
            self.assertTrue(restored.accepted)

            current["pair"] = pairs[0]
            self.assertFalse(await reconstructed.refresh())
            self.assertEqual(
                reconstructed.snapshot()["last_registry_failure_category"],
                "upstream_registry_rollback",
            )

            current["pair"] = (
                fixture.paths.registry.read_bytes(),
                canonical_json(
                    {
                        "schema_version": 1,
                        "algorithm": "Ed25519",
                        "key_id": "test-only-registry-key-v1",
                        "signature": "invalid",
                    }
                ),
            )
            clock["now"] += timedelta(seconds=CACHE_HARD_AGE_SECONDS + 1)
            self.assertFalse(await reconstructed.refresh())
            self.assertFalse(
                reconstructed.has_exact_attestation("ha-mcp", "7.14.2")
            )
            rejected = decide_admission(
                server_name="ha-mcp",
                server_version="7.14.2",
                protocol_version="2025-03-26",
                tool=json.loads(CONTRACT_PATH.read_text()),
                attestations=reconstructed.effective_attestations(),
            )
            self.assertFalse(rejected.accepted)
            self.assertEqual(
                reconstructed.snapshot()["last_registry_failure_category"],
                "upstream_registry_invalid_signature",
            )

            fixture.mutate("renew", 3, now=clock["now"])
            current["pair"] = (
                fixture.paths.registry.read_bytes(),
                fixture.paths.signature.read_bytes(),
            )
            self.assertTrue(await reconstructed.refresh())
            self.assertTrue(reconstructed.has_exact_attestation("ha-mcp", "7.14.2"))
            self.assertEqual(reconstructed.snapshot()["registry_sequence"], 4)

    def test_fixed_production_paths_and_no_runtime_url_option(self):
        self.assertTrue(REGISTRY_URL.endswith("upstream-dashboard-registry.json"))
        self.assertTrue(SIGNATURE_URL.endswith("upstream-dashboard-registry.sig.json"))
        configuration = (BETA / "ha_mcp_engineering" / "configuration.py").read_text()
        config_yaml = (BETA / "config.yaml").read_text()
        self.assertNotIn("upstream_trust_registry_url", configuration)
        self.assertNotIn("upstream_trust_registry_url", config_yaml)
        registry_source = (
            BETA / "ha_mcp_engineering" / "providers" / "upstream_registry.py"
        ).read_text()
        self.assertIn("allow_redirects=False", registry_source)


if __name__ == "__main__":
    unittest.main()
