import asyncio
import base64
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    DashboardTransportError,
    McpDashboardHandshake,
    McpDashboardRead,
    validate_dashboard_read_arguments,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.providers.upstream_contracts import (  # noqa: E402
    CONTRACT_FAMILY,
    COMPILED_CONTRACT_FAMILIES,
    COMPILED_ARGUMENT_SHAPES,
    PROHIBITED_ARGUMENTS,
    ReleaseAttestation,
    canonical_json,
    decide_admission,
    load_attestations,
    normalize_runtime_contract,
)
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    UpstreamDashboardProvider,
    ensure_dashboard_tool_allowed,
    _upstream_config_hash as _upstream_hash,
)
from ha_mcp_engineering.providers.upstream_registry import (  # noqa: E402
    MAX_REGISTRY_BYTES,
    RegistryValidationError,
    UpstreamTrustRegistry,
    verify_registry,
)


CONTRACTS = BETA / "ha_mcp_engineering" / "providers" / "contracts"
SEVEN_THIRTEEN = CONTRACTS / "ha_mcp_7_13_dashboard_read_v1.json"
SEVEN_FOURTEEN = CONTRACTS / "ha_mcp_7_14_dashboard_read_v2.json"


def tool_for(version):
    path = SEVEN_THIRTEEN if version == "7.13.0" else SEVEN_FOURTEEN
    return json.loads(path.read_text(encoding="utf-8"))


def handshake(version, tool=None, *, name="ha-mcp", extra_tools=()):
    return McpDashboardHandshake(
        protocol_version="2025-03-26",
        server_name=name,
        server_version=version,
        tools=(tool or tool_for(version), *extra_tools),
        connection_latency_ms=1.0,
    )


def settings():
    return Settings(
        ha_url="http://supervisor/core",
        ha_token="synthetic-supervisor-token",
        access_secret="synthetic-engineering-secret-value",
        port=8100,
        audit_path="audit.jsonl",
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        upstream_dashboard_mcp_url="http://upstream:9583/synthetic-secret/mcp",
    )


def tool_result(payload):
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "isError": False,
    }


class FakeTransport:
    def __init__(self, version, payload):
        self.handshake = handshake(version)
        self.payload = payload
        self.arguments = []
        self.other_tool_dispatches = []

    async def discover(self):
        return self.handshake

    async def execute_dashboard_read(self, arguments, validator):
        validator(self.handshake)
        self.arguments.append(copy.deepcopy(arguments))
        return McpDashboardRead(
            handshake=self.handshake,
            call_result=tool_result(self.payload),
            tool_call_latency_ms=2.0,
        )


def signed_registry(private_key, *, sequence, entries, now, expires=None):
    registry = {
        "schema_version": 1,
        "sequence": sequence,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (expires or now + timedelta(days=2))
        .isoformat()
        .replace("+00:00", "Z"),
        "key_id": "test-only-key-v1",
        "entries": entries,
    }
    signature = {
        "schema_version": 1,
        "algorithm": "Ed25519",
        "key_id": registry["key_id"],
        "signature": base64.b64encode(private_key.sign(canonical_json(registry))).decode(),
    }
    return canonical_json(registry), canonical_json(signature)


def public_key_text(private_key):
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode()


class ContractFamilyTests(unittest.TestCase):
    def test_all_reviewed_versions_have_exact_builtin_attestations(self):
        entries = {item.upstream_version: item for item in load_attestations()}
        self.assertEqual(set(entries), {"7.13.0", "7.14.0", "7.14.1"})
        for version in entries:
            contract = normalize_runtime_contract(
                tool_for(version), protocol_version="2025-03-26"
            )
            decision = decide_admission(
                server_name="ha-mcp",
                server_version=version,
                protocol_version="2025-03-26",
                tool=tool_for(version),
                attestations=((entries[version], "builtin"),),
            )
            self.assertTrue(decision.accepted)
            self.assertEqual(decision.status, "admitted_builtin_attestation")
            self.assertEqual(decision.contract_family, CONTRACT_FAMILY)
            self.assertEqual(
                contract.runtime_fingerprint,
                entries[version].runtime_contract_fingerprint,
            )

    def test_descriptions_titles_property_order_and_catalog_drift_are_ignored(self):
        original = tool_for("7.14.1")
        changed = copy.deepcopy(original)
        changed["description"] = "entirely different prose"
        changed["title"] = "Different display title"
        changed["annotations"]["title"] = "Different annotation title"
        changed["inputSchema"]["description"] = "ignored schema prose"
        changed["inputSchema"]["properties"] = dict(
            reversed(list(changed["inputSchema"]["properties"].items()))
        )
        first = normalize_runtime_contract(original, protocol_version="2025-03-26")
        second = normalize_runtime_contract(changed, protocol_version="2025-03-26")
        self.assertEqual(first, second)
        unrelated = {
            "name": "ha_set_entity",
            "annotations": {"destructiveHint": True},
            "inputSchema": {"type": "object"},
        }
        decision = decide_admission(
            server_name="ha-mcp",
            server_version="7.14.1",
            protocol_version="2025-03-26",
            tool=changed,
            attestations=tuple((entry, "builtin") for entry in load_attestations()),
        )
        self.assertTrue(decision.accepted)
        self.assertNotIn(unrelated, [changed])

    def test_security_relevant_drift_fails_closed(self):
        cases = {}
        screenshot = tool_for("7.14.1")
        screenshot["inputSchema"]["properties"]["include_screenshot"]["default"] = True
        cases["screenshot_default"] = screenshot
        permissive = tool_for("7.14.1")
        permissive["inputSchema"]["additionalProperties"] = True
        cases["additional_properties"] = permissive
        preference = tool_for("7.14.1")
        preference["inputSchema"]["properties"]["theme"] = {
            "type": "string",
            "default": "system",
        }
        cases["preference_argument"] = preference
        destructive = tool_for("7.14.1")
        destructive["annotations"]["destructiveHint"] = True
        cases["destructive_annotation"] = destructive
        output = tool_for("7.14.1")
        output["outputSchema"] = {"type": "object"}
        cases["output_schema"] = output
        required = tool_for("7.14.1")
        required["inputSchema"]["required"] = ["mode"]
        cases["required_argument"] = required
        for name, tool in cases.items():
            with self.subTest(name=name):
                decision = decide_admission(
                    server_name="ha-mcp",
                    server_version="7.14.1",
                    protocol_version="2025-03-26",
                    tool=tool,
                    attestations=tuple(
                        (entry, "builtin") for entry in load_attestations()
                    ),
                )
                self.assertFalse(decision.accepted)

    def test_unknown_version_family_tool_and_server_fail_closed(self):
        entries = tuple((entry, "builtin") for entry in load_attestations())
        self.assertEqual(
            decide_admission(
                server_name="ha-mcp",
                server_version="7.14.2",
                protocol_version="2025-03-26",
                tool=tool_for("7.14.1"),
                attestations=entries,
            ).status,
            "rejected_unknown_release",
        )
        self.assertEqual(
            decide_admission(
                server_name="HA-MCP",
                server_version="7.14.1",
                protocol_version="2025-03-26",
                tool=tool_for("7.14.1"),
                attestations=entries,
            ).status,
            "rejected_contract_mismatch",
        )
        renamed = tool_for("7.14.1")
        renamed["name"] = "ha_config_set_dashboard"
        renamed_decision = decide_admission(
            server_name="ha-mcp",
            server_version="7.14.1",
            protocol_version="2025-03-26",
            tool=renamed,
            attestations=entries,
        )
        self.assertEqual(renamed_decision.status, "rejected_contract_mismatch")
        self.assertEqual(renamed_decision.failure_category, "required_tool_missing")

    def test_registry_data_cannot_expand_compiled_capabilities(self):
        self.assertEqual(set(COMPILED_ARGUMENT_SHAPES), {"list_dashboards", "get_dashboard_config"})
        self.assertEqual(set(COMPILED_CONTRACT_FAMILIES), {CONTRACT_FAMILY})
        for tool_name in (
            "ha_set_entity",
            "ha_set_device",
            "call_service",
            "ha_call_service",
            "ha_bulk_control",
            "ha_config_set_dashboard",
            "ha_config_delete_dashboard",
            "ha_config_set_blueprint",
        ):
            with self.subTest(tool_name=tool_name):
                with self.assertRaises(Exception):
                    ensure_dashboard_tool_allowed(tool_name)
        self.assertTrue({"theme", "mode", "query", "view_path"}.issubset(PROHIBITED_ARGUMENTS))
        with self.assertRaises(DashboardTransportError):
            validate_dashboard_read_arguments(
                {"list_only": True, "include_screenshot": False, "mode": "search"}
            )


class ProviderAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_positive_list_get_and_not_found_for_all_builtins(self):
        for version in ("7.13.0", "7.14.0", "7.14.1"):
            with self.subTest(version=version):
                transport = FakeTransport(
                    version,
                    {"success": True, "action": "list", "dashboards": [], "count": 0},
                )
                provider = UpstreamDashboardProvider()
                provider.configure(settings(), transport=transport)
                result = await provider.list_dashboards(limit=10, response_limit=60_000)
                self.assertEqual(result.data["dashboards"], [])
                self.assertEqual(
                    transport.arguments,
                    [{"list_only": True, "include_screenshot": False}],
                )
                health = provider.health_snapshot()
                self.assertEqual(health["admission_status"], "admitted_builtin_attestation")
                self.assertEqual(health["contract_family"], CONTRACT_FAMILY)
                self.assertEqual(health["allowlisted_tool_count"], 1)
                self.assertFalse(health["screenshots_allowed"])
                self.assertFalse(health["preference_writes_allowed"])
                self.assertFalse(health["writes_allowed"])

                config = {"title": "Compatibility", "views": []}
                get_transport = FakeTransport(
                    version,
                    {
                        "success": True,
                        "action": "get",
                        "url_path": "compatibility",
                        "config": config,
                        "config_hash": _upstream_hash(config),
                    },
                )
                get_provider = UpstreamDashboardProvider()
                get_provider.configure(settings(), transport=get_transport)
                result = await get_provider.get_dashboard_config(
                    url_path="compatibility",
                    force_reload=True,
                    response_limit=60_000,
                )
                self.assertEqual(result.data["config_hash"], _upstream_hash(config))
                self.assertEqual(len(result.data["engineering_config_hash"]), 64)

    async def test_exact_get_builder_never_forwards_new_optional_arguments(self):
        config = {"views": [{"title": "Home", "cards": []}]}
        from ha_mcp_engineering.providers.upstream_dashboard import _upstream_config_hash

        transport = FakeTransport(
            "7.14.1",
            {
                "success": True,
                "action": "get",
                "url_path": "home",
                "config": config,
                "config_hash": _upstream_config_hash(config),
            },
        )
        provider = UpstreamDashboardProvider()
        provider.configure(settings(), transport=transport)
        await provider.get_dashboard_config(
            url_path="home", force_reload=True, response_limit=60_000
        )
        self.assertEqual(
            transport.arguments,
            [
                {
                    "url_path": "home",
                    "list_only": False,
                    "force_reload": True,
                    "include_screenshot": False,
                }
            ],
        )


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = public_key_text(self.private_key)
        self.now = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)

    def future_entry(self, *, version="7.14.2", revoked=False):
        base = load_attestations()[-1]
        value = dict(base.__dict__)
        value.update(
            {
                "entry_id": f"ha-mcp-v{version}-synthetic",
                "upstream_version": version,
                "source_tag": f"v{version}",
                "source_commit": "1" * 40,
                "image_index_digest": "sha256:" + "2" * 64,
                "platform_digests": {
                    "linux/amd64": "sha256:" + "3" * 64,
                    "linux/arm64": "sha256:" + "4" * 64,
                },
                "image_revision": "5" * 40,
                "review_evidence_digest": "sha256:" + "6" * 64,
                "catalog_fingerprint": None,
                "reviewed_at": "2026-07-20T00:00:00Z",
                "revoked": revoked,
            }
        )
        return value

    def test_signature_validation_and_tamper_detection(self):
        registry, signature = signed_registry(
            self.private_key,
            sequence=1,
            entries=[self.future_entry()],
            now=self.now,
        )
        key = self.private_key.public_key()
        verified = verify_registry(
            registry, signature, public_key=key, now=self.now, source="test"
        )
        self.assertEqual(verified.sequence, 1)
        tampered = json.loads(registry)
        tampered["sequence"] = 2
        with self.assertRaisesRegex(RegistryValidationError, "invalid_signature"):
            verify_registry(
                canonical_json(tampered),
                signature,
                public_key=key,
                now=self.now,
                source="test",
            )

    def test_public_key_and_fixed_registry_locations_are_not_exposed_by_health(self):
        provider_settings = replace(
            settings(),
            upstream_trust_registry_enabled=True,
            upstream_trust_registry_public_key=self.public_key,
        )
        with tempfile.TemporaryDirectory() as directory:
            manager = UpstreamTrustRegistry(
                enabled=True,
                public_key=self.public_key,
                cache_path=Path(directory) / "cache.json",
                now=lambda: self.now,
            )
            provider = UpstreamDashboardProvider()
            provider.configure(
                provider_settings,
                transport=FakeTransport(
                    "7.14.1",
                    {"success": True, "action": "list", "dashboards": []},
                ),
                registry=manager,
            )
            serialized = json.dumps(provider.health_snapshot())
        self.assertNotIn(self.public_key, serialized)
        self.assertNotIn("raw.githubusercontent.com", serialized)
        self.assertNotIn("upstream-dashboard-trust-registry-cache", serialized)
        self.assertIn("fixed_repository_https", serialized)

    def test_expired_registry_is_rejected(self):
        registry, signature = signed_registry(
            self.private_key,
            sequence=1,
            entries=[self.future_entry()],
            now=self.now - timedelta(days=4),
            expires=self.now - timedelta(days=1),
        )
        with self.assertRaisesRegex(RegistryValidationError, "expired"):
            verify_registry(
                registry,
                signature,
                public_key=self.private_key.public_key(),
                now=self.now,
                source="test",
            )
        wrong_key = Ed25519PrivateKey.generate().public_key()
        with self.assertRaisesRegex(RegistryValidationError, "invalid_signature"):
            verify_registry(
                registry,
                signature,
                public_key=wrong_key,
                now=self.now,
                source="test",
            )

    async def test_atomic_cache_lkg_rollback_and_revocation(self):
        responses = []
        for sequence, revoked in ((2, False), (1, False), (3, True)):
            responses.append(
                signed_registry(
                    self.private_key,
                    sequence=sequence,
                    entries=[self.future_entry(revoked=revoked)],
                    now=self.now,
                )
            )
        current = {"pair": responses[0]}

        async def fetch(url, maximum):
            return current["pair"][1 if url.endswith("sig.json") else 0]

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "registry-cache.json"
            manager = UpstreamTrustRegistry(
                enabled=True,
                public_key=self.public_key,
                cache_path=cache,
                fetcher=fetch,
                now=lambda: self.now,
            )
            self.assertTrue(await manager.refresh())
            self.assertTrue(cache.exists())
            self.assertTrue(manager.has_exact_attestation("ha-mcp", "7.14.2"))
            current["pair"] = responses[1]
            self.assertFalse(await manager.refresh())
            self.assertEqual(manager.snapshot()["last_registry_failure_category"], "upstream_registry_rollback")
            current["pair"] = responses[2]
            self.assertTrue(await manager.refresh())
            selected = [
                entry
                for entry, _source in manager.effective_attestations()
                if entry.upstream_version == "7.14.2"
            ][0]
            self.assertTrue(selected.revoked)

    async def test_invalid_refresh_preserves_cached_registry(self):
        good = signed_registry(
            self.private_key,
            sequence=4,
            entries=[self.future_entry()],
            now=self.now,
        )
        current = {"pair": good}

        async def fetch(url, maximum):
            return current["pair"][1 if url.endswith("sig.json") else 0]

        with tempfile.TemporaryDirectory() as directory:
            manager = UpstreamTrustRegistry(
                enabled=True,
                public_key=self.public_key,
                cache_path=Path(directory) / "cache.json",
                fetcher=fetch,
                now=lambda: self.now,
            )
            self.assertTrue(await manager.refresh())
            current["pair"] = (b"{}", b"{}")
            self.assertFalse(await manager.refresh())
            self.assertTrue(manager.has_exact_attestation("ha-mcp", "7.14.2"))

            restarted = UpstreamTrustRegistry(
                enabled=True,
                public_key=self.public_key,
                cache_path=Path(directory) / "cache.json",
                fetcher=fetch,
                now=lambda: self.now,
            )
            self.assertTrue(restarted.has_exact_attestation("ha-mcp", "7.14.2"))
            self.assertEqual(restarted.snapshot()["cache_status"], "valid")

    async def test_equal_sequence_with_different_content_is_rejected(self):
        first = signed_registry(
            self.private_key,
            sequence=7,
            entries=[self.future_entry(version="7.14.2")],
            now=self.now,
        )
        conflicting = signed_registry(
            self.private_key,
            sequence=7,
            entries=[self.future_entry(version="7.14.3")],
            now=self.now,
        )
        current = {"pair": first}

        async def fetch(url, maximum):
            return current["pair"][1 if url.endswith("sig.json") else 0]

        with tempfile.TemporaryDirectory() as directory:
            manager = UpstreamTrustRegistry(
                enabled=True,
                public_key=self.public_key,
                cache_path=Path(directory) / "cache.json",
                fetcher=fetch,
                now=lambda: self.now,
            )
            self.assertTrue(await manager.refresh())
            current["pair"] = conflicting
            self.assertFalse(await manager.refresh())
            self.assertEqual(
                manager.snapshot()["last_registry_failure_category"],
                "upstream_registry_replay_conflict",
            )
            self.assertTrue(manager.has_exact_attestation("ha-mcp", "7.14.2"))
            self.assertFalse(manager.has_exact_attestation("ha-mcp", "7.14.3"))

    async def test_signed_future_entry_and_higher_sequence_revocation_need_no_code_change(self):
        future = self.future_entry()
        accepted_raw = signed_registry(
            self.private_key,
            sequence=10,
            entries=[future],
            now=self.now,
        )
        revoked_raw = signed_registry(
            self.private_key,
            sequence=11,
            entries=[self.future_entry(revoked=True)],
            now=self.now,
        )
        current = {"pair": accepted_raw}

        async def fetch(url, maximum):
            return current["pair"][1 if url.endswith("sig.json") else 0]

        with tempfile.TemporaryDirectory() as directory:
            manager = UpstreamTrustRegistry(
                enabled=True,
                public_key=self.public_key,
                cache_path=Path(directory) / "cache.json",
                fetcher=fetch,
                now=lambda: self.now,
            )
            self.assertTrue(await manager.refresh())
            admitted = decide_admission(
                server_name="ha-mcp",
                server_version="7.14.2",
                protocol_version="2025-03-26",
                tool=tool_for("7.14.1"),
                attestations=manager.effective_attestations(),
            )
            self.assertTrue(admitted.accepted)
            self.assertEqual(admitted.status, "admitted_signed_registry_attestation")
            current["pair"] = revoked_raw
            self.assertTrue(await manager.refresh())
            rejected = decide_admission(
                server_name="ha-mcp",
                server_version="7.14.2",
                protocol_version="2025-03-26",
                tool=tool_for("7.14.1"),
                attestations=manager.effective_attestations(),
            )
            self.assertFalse(rejected.accepted)
            self.assertEqual(rejected.status, "rejected_revoked_attestation")

    def test_duplicate_json_keys_and_unknown_fields_rejected(self):
        signature = canonical_json(
            {"schema_version": 1, "algorithm": "Ed25519", "key_id": "x", "signature": ""}
        )
        with self.assertRaises(RegistryValidationError):
            verify_registry(
                b'{"schema_version":1,"schema_version":1}',
                signature,
                public_key=self.private_key.public_key(),
                now=self.now,
                source="test",
            )

    def test_malformed_oversized_unknown_family_and_conflicts_reject(self):
        invalid_signature = canonical_json(
            {
                "schema_version": 1,
                "algorithm": "Ed25519",
                "key_id": "test-only-key-v1",
                "signature": "not-base64!",
            }
        )
        with self.assertRaises(RegistryValidationError):
            verify_registry(
                b"{}",
                invalid_signature,
                public_key=self.private_key.public_key(),
                now=self.now,
                source="test",
            )
        with self.assertRaisesRegex(RegistryValidationError, "oversized"):
            verify_registry(
                b"{" + b" " * MAX_REGISTRY_BYTES + b"}",
                invalid_signature,
                public_key=self.private_key.public_key(),
                now=self.now,
                source="test",
            )

        unknown = self.future_entry()
        unknown["contract_family"] = "signed_data_must_not_define_this"
        duplicate = self.future_entry()
        for entries in ([unknown], [duplicate, copy.deepcopy(duplicate)]):
            registry, signature = signed_registry(
                self.private_key,
                sequence=20,
                entries=entries,
                now=self.now,
            )
            with self.assertRaises(RegistryValidationError):
                verify_registry(
                    registry,
                    signature,
                    public_key=self.private_key.public_key(),
                    now=self.now,
                    source="test",
                )

    async def test_arbitrary_registry_location_and_remote_timeout_fail_closed(self):
        manager = UpstreamTrustRegistry(
            enabled=True,
            public_key=self.public_key,
            now=lambda: self.now,
        )
        with self.assertRaisesRegex(RegistryValidationError, "location_rejected"):
            await manager._fetch_bytes("http://unapproved.invalid/registry.json", 100)

        async def timeout(_url, _maximum):
            raise asyncio.TimeoutError

        with tempfile.TemporaryDirectory() as directory:
            timed = UpstreamTrustRegistry(
                enabled=True,
                public_key=self.public_key,
                cache_path=Path(directory) / "cache.json",
                fetcher=timeout,
                now=lambda: self.now,
            )
            self.assertFalse(await timed.refresh())
            self.assertEqual(
                timed.snapshot()["last_registry_failure_category"],
                "upstream_registry_unavailable",
            )


if __name__ == "__main__":
    unittest.main()
