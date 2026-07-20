import copy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    DashboardTransportError,
    McpDashboardHandshake,
    McpDashboardRead,
    REQUIRED_DASHBOARD_TOOL,
    validate_dashboard_read_arguments,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    REVIEWED_ANNOTATIONS,
    REVIEWED_FIXTURE_RUNTIME_DESCRIPTOR_FINGERPRINT,
    REVIEWED_PUBLISHED_RUNTIME_DESCRIPTOR_FINGERPRINT,
    REVIEWED_PROTOCOL_VERSION,
    REVIEWED_SCHEMA_FINGERPRINT,
    REVIEWED_SECURITY_CONTRACT_FINGERPRINT,
    REVIEWED_SERVER_NAME,
    REVIEWED_SERVER_VERSION,
    REVIEWED_TRUST_PROFILE,
    TRUST_MODE_CONTRACT_READ_ONLY,
    TRUST_MODE_REVIEWED_ARGUMENT_CONSTRAINED,
    DashboardProviderResult,
    UpstreamDashboardProvider,
    _reviewed_security_contract_projection,
    _stable_hash,
    _upstream_config_hash,
    ensure_dashboard_tool_allowed,
)


FIXTURE_PATH = (
    BETA
    / "ha_mcp_engineering"
    / "providers"
    / "contracts"
    / "ha_mcp_7_13_dashboard_read_v1.json"
)
RUNTIME_DELTA_PATH = (
    BETA
    / "ha_mcp_engineering"
    / "providers"
    / "contracts"
    / "ha_mcp_7_13_published_runtime_delta.json"
)
SECRET_URL = "http://ha-mcp:9583/synthetic-reviewed-secret/mcp"


def fixture_tool():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def published_runtime_tool():
    tool = fixture_tool()
    delta = json.loads(RUNTIME_DELTA_PATH.read_text(encoding="utf-8"))
    added = delta["descriptor_delta"]["added"]
    if set(added) != {"/_meta/ha_mcp"}:
        raise AssertionError("published runtime delta contains an unreviewed path")
    tool.setdefault("_meta", {})["ha_mcp"] = added["/_meta/ha_mcp"]
    return tool


def reviewed_handshake(
    *,
    name=REVIEWED_SERVER_NAME,
    version=REVIEWED_SERVER_VERSION,
    protocol=REVIEWED_PROTOCOL_VERSION,
    tool=None,
):
    return McpDashboardHandshake(
        protocol_version=protocol,
        server_name=name,
        server_version=version,
        tools=(tool or published_runtime_tool(),),
        connection_latency_ms=1.25,
    )


def settings():
    return Settings(
        ha_url="http://supervisor/core",
        ha_token="synthetic-supervisor-token",
        access_secret="synthetic-access-secret-value",
        port=8100,
        audit_path="audit.jsonl",
        rate_limit_per_minute=120,
        rate_limit_burst=25,
        destructive_services=frozenset(),
        response_size_limit=60_000,
        upstream_dashboard_mcp_url=SECRET_URL,
    )


def call_result(payload):
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "isError": False,
    }


class ReviewedFakeTransport:
    def __init__(self, handshake=None, payload=None):
        self.handshake = handshake or reviewed_handshake()
        self.payload = payload or {
            "success": True,
            "action": "list",
            "dashboards": [],
            "count": 0,
        }
        self.arguments = []
        self.tool_dispatch_count = 0

    async def discover(self):
        return self.handshake

    async def execute_dashboard_read(self, arguments, validator):
        validator(self.handshake)
        self.arguments.append(copy.deepcopy(arguments))
        self.tool_dispatch_count += 1
        return McpDashboardRead(
            handshake=self.handshake,
            call_result=call_result(self.payload),
            tool_call_latency_ms=2.0,
        )


class ReviewedIdentityAndVersionTests(unittest.IsolatedAsyncioTestCase):
    async def _failure(self, *, name=REVIEWED_SERVER_NAME, version=REVIEWED_SERVER_VERSION):
        transport = ReviewedFakeTransport(
            handshake=reviewed_handshake(name=name, version=version)
        )
        provider = UpstreamDashboardProvider()
        provider.configure(settings(), transport=transport)
        with self.assertRaises(Exception) as caught:
            await provider.refresh_capabilities()
        self.assertEqual(transport.tool_dispatch_count, 0)
        return caught.exception, provider.health_snapshot()

    async def test_exact_reviewed_identity_version_and_contract_are_accepted(self):
        provider = UpstreamDashboardProvider()
        provider.configure(settings(), transport=ReviewedFakeTransport())
        await provider.refresh_capabilities()
        health = provider.health_snapshot()
        self.assertEqual(
            health["trust_mode"], TRUST_MODE_REVIEWED_ARGUMENT_CONSTRAINED
        )
        self.assertEqual(health["trust_profile"], REVIEWED_TRUST_PROFILE)
        self.assertTrue(health["reviewed_contract_match"])
        self.assertTrue(health["input_schema_match"])
        self.assertTrue(health["reviewed_security_contract_match"])
        self.assertFalse(health["runtime_descriptor_match"])
        self.assertTrue(health["published_runtime_descriptor_match"])
        self.assertEqual(
            health["runtime_descriptor_drift"],
            "descriptive_metadata_only",
        )
        self.assertTrue(health["required_schema_compatible"])

    async def test_different_missing_case_variant_and_alias_names_are_rejected(self):
        for name in (
            "other-mcp",
            "",
            None,
            "HA-MCP",
            "homeassistant-ai/ha-mcp",
        ):
            with self.subTest(name=name):
                exc, health = await self._failure(name=name)
                self.assertEqual(
                    exc.code,
                    ErrorCode.UPSTREAM_DASHBOARD_SERVER_IDENTITY_MISMATCH,
                )
                self.assertEqual(
                    health["validation_reason"], "server_identity_mismatch"
                )

    async def test_unattested_missing_and_malformed_versions_are_rejected(self):
        for version in ("7.12.3", "7.13.1", "7.14.2", "", None, "release-latest"):
            with self.subTest(version=version):
                exc, health = await self._failure(version=version)
                self.assertEqual(
                    exc.code,
                    ErrorCode.UPSTREAM_DASHBOARD_VERSION_MISMATCH,
                )
                self.assertEqual(
                    health["validation_reason"], "upstream_attestation_missing"
                )

    async def test_incompatible_protocol_is_rejected(self):
        provider = UpstreamDashboardProvider()
        provider.configure(
            settings(),
            transport=ReviewedFakeTransport(
                handshake=reviewed_handshake(protocol="2024-11-05")
            ),
        )
        with self.assertRaises(Exception) as caught:
            await provider.refresh_capabilities()
        self.assertEqual(
            caught.exception.code,
            ErrorCode.UPSTREAM_DASHBOARD_UNSUPPORTED_TRUST_PROFILE,
        )


class ReviewedContractTests(unittest.IsolatedAsyncioTestCase):
    def test_fixture_reproduces_known_runtime_schema_and_security_fingerprints(self):
        tool = fixture_tool()
        self.assertEqual(
            _stable_hash(tool),
            REVIEWED_FIXTURE_RUNTIME_DESCRIPTOR_FINGERPRINT,
        )
        self.assertEqual(
            _stable_hash(tool["inputSchema"]),
            REVIEWED_SCHEMA_FINGERPRINT,
        )
        self.assertEqual(
            _stable_hash(_reviewed_security_contract_projection(tool)),
            REVIEWED_SECURITY_CONTRACT_FINGERPRINT,
        )
        self.assertEqual(tool["annotations"], REVIEWED_ANNOTATIONS)

    def test_published_artifact_delta_reproduces_observed_runtime_fingerprint(self):
        delta = json.loads(RUNTIME_DELTA_PATH.read_text(encoding="utf-8"))
        tool = published_runtime_tool()
        self.assertEqual(
            delta["artifact"]["reviewed_source_revision"],
            "f4eb53621ccb814cb7123d2811e06eda3577129c",
        )
        self.assertEqual(
            delta["artifact"]["index_digest"],
            "sha256:f6c0d3379b625687757f55be51e786ecbc46ab7ad96c994208aec9dc2344396a",
        )
        self.assertEqual(
            _stable_hash(tool),
            REVIEWED_PUBLISHED_RUNTIME_DESCRIPTOR_FINGERPRINT,
        )
        self.assertEqual(
            _stable_hash(tool["inputSchema"]),
            REVIEWED_SCHEMA_FINGERPRINT,
        )
        self.assertEqual(
            _stable_hash(_reviewed_security_contract_projection(tool)),
            REVIEWED_SECURITY_CONTRACT_FINGERPRINT,
        )
        self.assertEqual(
            delta["descriptor_delta"],
            {
                "added": {
                    "/_meta/ha_mcp": {
                        "llm_api_exposed": True,
                        "pinned": False,
                    }
                },
                "removed": {},
                "changed": {},
            },
        )

    def test_dictionary_and_property_order_do_not_change_fingerprints(self):
        reordered = json.loads(
            FIXTURE_PATH.read_text(encoding="utf-8"),
            object_pairs_hook=lambda pairs: dict(reversed(pairs)),
        )
        self.assertEqual(
            _stable_hash(reordered),
            REVIEWED_FIXTURE_RUNTIME_DESCRIPTOR_FINGERPRINT,
        )
        self.assertEqual(
            _stable_hash(_reviewed_security_contract_projection(reordered)),
            REVIEWED_SECURITY_CONTRACT_FINGERPRINT,
        )

    async def test_fixture_and_published_runtime_both_pass_security_gate(self):
        for label, tool in (
            ("reviewed_fixture", fixture_tool()),
            ("published_runtime", published_runtime_tool()),
        ):
            with self.subTest(label=label):
                provider = UpstreamDashboardProvider()
                provider.configure(
                    settings(),
                    transport=ReviewedFakeTransport(
                        handshake=reviewed_handshake(tool=tool)
                    ),
                )
                await provider.refresh_capabilities()
                health = provider.health_snapshot()
                self.assertTrue(health["input_schema_match"])
                self.assertTrue(health["reviewed_security_contract_match"])
                self.assertEqual(health["capability_status"], "available")

    async def test_descriptive_and_presentation_drift_is_non_blocking(self):
        cases = {}
        description = published_runtime_tool()
        description["description"] = "Reworded operator documentation."
        cases["description"] = description
        title = published_runtime_tool()
        title["title"] = "Dashboard Evidence"
        cases["title"] = title
        annotation_title = published_runtime_tool()
        annotation_title["annotations"]["title"] = "Dashboard Evidence"
        cases["annotation_title"] = annotation_title
        exposure = published_runtime_tool()
        exposure["_meta"]["ha_mcp"] = {
            "llm_api_exposed": False,
            "pinned": True,
        }
        cases["conversation_exposure"] = exposure

        for label, tool in cases.items():
            with self.subTest(label=label):
                self.assertNotEqual(
                    _stable_hash(tool),
                    REVIEWED_FIXTURE_RUNTIME_DESCRIPTOR_FINGERPRINT,
                )
                self.assertEqual(
                    _stable_hash(_reviewed_security_contract_projection(tool)),
                    REVIEWED_SECURITY_CONTRACT_FINGERPRINT,
                )
                provider = UpstreamDashboardProvider()
                provider.configure(
                    settings(),
                    transport=ReviewedFakeTransport(
                        handshake=reviewed_handshake(tool=tool)
                    ),
                )
                await provider.refresh_capabilities()
                health = provider.health_snapshot()
                self.assertTrue(health["reviewed_security_contract_match"])
                self.assertFalse(health["runtime_descriptor_match"])
                self.assertEqual(
                    health["runtime_descriptor_drift"],
                    "descriptive_metadata_only",
                )

    async def _assert_contract_failure(self, tool, expected_reason):
        provider = UpstreamDashboardProvider()
        transport = ReviewedFakeTransport(handshake=reviewed_handshake(tool=tool))
        provider.configure(settings(), transport=transport)
        with self.assertRaises(Exception) as caught:
            await provider.refresh_capabilities()
        self.assertEqual(transport.tool_dispatch_count, 0)
        self.assertEqual(
            provider.health_snapshot()["validation_reason"],
            expected_reason,
        )
        return caught.exception

    async def test_input_contract_drift_fails_closed(self):
        mutations = {}

        added = fixture_tool()
        added["inputSchema"]["properties"]["future_optional"] = {
            "type": "string",
            "default": "",
        }
        mutations["new_optional"] = added

        removed = fixture_tool()
        removed["inputSchema"]["properties"].pop("view_path")
        mutations["removed_argument"] = removed

        changed = fixture_tool()
        changed["inputSchema"]["properties"]["url_path"] = {"type": "integer"}
        mutations["changed_type"] = changed

        required = fixture_tool()
        required["inputSchema"]["required"] = ["future_required"]
        mutations["new_required"] = required

        for label, tool in mutations.items():
            with self.subTest(label=label):
                exc = await self._assert_contract_failure(
                    tool, "upstream_input_contract_mismatch"
                )
                self.assertEqual(
                    exc.code,
                    ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
                )

    async def test_annotation_drift_fails_closed(self):
        cases = {
            "destructive_true": {"destructiveHint": True},
            "idempotent_false": {"idempotentHint": False},
            "idempotent_missing": {"idempotentHint": None},
            "open_world_true": {"openWorldHint": True},
            "read_only_false": {"readOnlyHint": False},
            "malformed_type": {"destructiveHint": "false"},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                tool = fixture_tool()
                for key, value in changes.items():
                    if value is None:
                        tool["annotations"].pop(key, None)
                    else:
                        tool["annotations"][key] = value
                exc = await self._assert_contract_failure(
                    tool, "upstream_security_contract_mismatch"
                )
                self.assertEqual(
                    exc.code,
                    ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_ANNOTATION_MISMATCH,
                )

    async def test_output_contract_and_unknown_semantic_metadata_fail_closed(self):
        output = published_runtime_tool()
        output["outputSchema"] = {
            "type": "object",
            "properties": {"config_hash": {"type": "string"}},
        }
        exc = await self._assert_contract_failure(
            output, "upstream_output_contract_mismatch"
        )
        self.assertEqual(
            exc.code,
            ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
        )

        metadata = published_runtime_tool()
        metadata["_meta"]["ha_mcp"]["future_operation_semantics"] = "changed"
        exc = await self._assert_contract_failure(
            metadata, "upstream_runtime_contract_mismatch"
        )
        self.assertEqual(
            exc.code,
            ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
        )

        semantic = published_runtime_tool()
        semantic["operationSemantics"] = "changed"
        exc = await self._assert_contract_failure(
            semantic, "upstream_runtime_contract_mismatch"
        )
        self.assertEqual(
            exc.code,
            ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
        )

    async def test_read_only_true_is_contract_drift_and_fails_closed(self):
        tool = fixture_tool()
        tool["annotations"]["readOnlyHint"] = True
        provider = UpstreamDashboardProvider()
        provider.configure(
            settings(),
            transport=ReviewedFakeTransport(
                handshake=reviewed_handshake(
                    name=REVIEWED_SERVER_NAME,
                    version=REVIEWED_SERVER_VERSION,
                    tool=tool,
                )
            ),
        )
        with self.assertRaises(Exception):
            await provider.refresh_capabilities()
        health = provider.health_snapshot()
        self.assertIsNone(health["trust_mode"])
        self.assertFalse(health["reviewed_contract_match"])
        self.assertEqual(
            health["validation_reason"],
            "upstream_security_contract_mismatch",
        )

    async def test_renamed_tool_is_rejected(self):
        tool = fixture_tool()
        tool["name"] = "ha_config_get_dashboard_alias"
        provider = UpstreamDashboardProvider()
        transport = ReviewedFakeTransport(
            handshake=McpDashboardHandshake(
                protocol_version=REVIEWED_PROTOCOL_VERSION,
                server_name=REVIEWED_SERVER_NAME,
                server_version=REVIEWED_SERVER_VERSION,
                tools=(tool,),
                connection_latency_ms=1.0,
            )
        )
        provider.configure(settings(), transport=transport)
        with self.assertRaises(Exception) as caught:
            await provider.refresh_capabilities()
        self.assertEqual(
            caught.exception.code,
            ErrorCode.UPSTREAM_DASHBOARD_REQUIRED_TOOL_MISSING,
        )


class ExactInvocationAndBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_inventory_sends_only_the_reviewed_read_shape(self):
        transport = ReviewedFakeTransport()
        provider = UpstreamDashboardProvider()
        provider.configure(settings(), transport=transport)
        await provider.list_dashboards(limit=10, response_limit=60_000)
        self.assertEqual(
            transport.arguments,
            [{"list_only": True, "include_screenshot": False}],
        )

    async def test_exact_config_sends_only_the_reviewed_read_shape(self):
        config = {"title": "Reviewed", "views": []}
        for force_reload in (True, False):
            with self.subTest(force_reload=force_reload):
                transport = ReviewedFakeTransport(
                    payload={
                        "success": True,
                        "action": "get",
                        "url_path": "reviewed-dashboard",
                        "config": config,
                        "config_hash": _upstream_config_hash(config),
                    }
                )
                provider = UpstreamDashboardProvider()
                provider.configure(settings(), transport=transport)
                result = await provider.get_dashboard_config(
                    url_path="reviewed-dashboard",
                    force_reload=force_reload,
                    response_limit=60_000,
                )
                self.assertIsInstance(result, DashboardProviderResult)
                self.assertEqual(
                    transport.arguments,
                    [
                        {
                            "url_path": "reviewed-dashboard",
                            "list_only": False,
                            "force_reload": force_reload,
                            "include_screenshot": False,
                        }
                    ],
                )

    def test_prohibited_rendering_and_arbitrary_shapes_are_rejected(self):
        cases = (
            {"list_only": True, "include_screenshot": True},
            {
                "url_path": "reviewed-dashboard",
                "list_only": False,
                "force_reload": True,
                "include_screenshot": True,
            },
            {
                "url_path": "reviewed-dashboard",
                "list_only": False,
                "force_reload": True,
                "include_screenshot": False,
                "view_path": "overview",
            },
            {"list_only": True, "include_screenshot": False, "full_page": True},
            {"list_only": True, "include_screenshot": False, "theme": "dark"},
            {"list_only": True, "include_screenshot": False, "dark_mode": True},
            {"list_only": True, "include_screenshot": False, "width": 1920},
            {"list_only": True, "include_screenshot": False, "arbitrary": "value"},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(DashboardTransportError) as caught:
                    validate_dashboard_read_arguments(arguments)
                self.assertEqual(caught.exception.category, "prohibited_argument")

    async def test_prohibited_argument_fails_before_transport_dispatch(self):
        transport = ReviewedFakeTransport()
        provider = UpstreamDashboardProvider()
        provider.configure(settings(), transport=transport)
        with self.assertRaises(Exception) as caught:
            await provider._execute(
                operation="prohibited_test",
                arguments={
                    "list_only": True,
                    "include_screenshot": True,
                },
                normalizer=lambda _payload, _exchange: None,
            )
        self.assertEqual(
            caught.exception.code,
            ErrorCode.UPSTREAM_DASHBOARD_PROHIBITED_ARGUMENT,
        )
        self.assertEqual(transport.tool_dispatch_count, 0)
        self.assertFalse(
            caught.exception.details["upstream_dispatch_occurred"]
        )

    def test_only_the_reviewed_tool_name_is_dispatchable(self):
        ensure_dashboard_tool_allowed(REQUIRED_DASHBOARD_TOOL)
        for name in (
            "ha_config_set_dashboard",
            "ha_config_delete_dashboard",
            "ha_manage_backup",
            "call_service",
            "reload_domain",
            "upsert_automation",
            "arbitrary_tool",
        ):
            with self.subTest(name=name):
                with self.assertRaises(GovernanceError):
                    ensure_dashboard_tool_allowed(name)

    async def test_allowed_calls_leave_simulated_preference_store_unchanged(self):
        preference_store = b'{"theme":"operator-default","dark_mode":false}'
        before = bytes(preference_store)

        class BranchingTransport(ReviewedFakeTransport):
            async def execute_dashboard_read(self, arguments, validator):
                validator(self.handshake)
                if arguments.get("include_screenshot") is True:
                    raise AssertionError("simulated preference write branch reached")
                self.arguments.append(copy.deepcopy(arguments))
                self.tool_dispatch_count += 1
                payload = self.payload
                return McpDashboardRead(
                    self.handshake,
                    call_result(payload),
                    tool_call_latency_ms=1.0,
                )

        inventory = BranchingTransport()
        provider = UpstreamDashboardProvider()
        provider.configure(settings(), transport=inventory)
        await provider.list_dashboards(limit=10, response_limit=60_000)

        config = {"views": []}
        exact = BranchingTransport(
            payload={
                "success": True,
                "action": "get",
                "url_path": "reviewed-dashboard",
                "config": config,
                "config_hash": _upstream_config_hash(config),
            }
        )
        provider.configure(settings(), transport=exact)
        await provider.get_dashboard_config(
            url_path="reviewed-dashboard",
            force_reload=True,
            response_limit=60_000,
        )
        self.assertEqual(preference_store, before)
        self.assertTrue(
            all(
                call.get("include_screenshot") is False
                for call in (*inventory.arguments, *exact.arguments)
            )
        )


if __name__ == "__main__":
    unittest.main()
