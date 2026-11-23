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
from ha_mcp_engineering.providers.upstream_contracts import CONTRACT_FAMILY_V3  # noqa: E402
from ha_mcp_engineering.providers.upstream_dashboard import (  # noqa: E402
    TRUST_MODE_CONTRACT_READ_ONLY,
    TRUST_MODE_REVIEWED_ARGUMENT_CONSTRAINED,
    DashboardProviderResult,
    UpstreamDashboardProvider,
    _reviewed_security_contract_projection,
    _stable_hash,
    _upstream_config_hash,
    ensure_dashboard_tool_allowed,
)


# HAMCP-437: re-pinned from the RC3A ha-mcp 7.13.0 contract fixture and its
# published-runtime delta to the exact ha-mcp 9.2.0 getter in the HAMCP-371 S8
# capture, the only reviewed release after HAMCP-437.
FIXTURE_PATH = ROOT / "docs" / "evidence" / "upstream-read-compatibility" / "ha-mcp-9.2.0.json"
REVIEWED_SERVER_NAME = "ha-mcp"
REVIEWED_SERVER_VERSION = "9.2.0"
REVIEWED_PROTOCOL_VERSION = "2025-03-26"
REVIEWED_TRUST_PROFILE = CONTRACT_FAMILY_V3
# The committed reply envelopes of the dashboard source, which has the same
# sha256 at v9.2.0 as at v9.1.0 (tests/test_hamcp319_dashboard, HAMCP-371 SB).
ENVELOPES = json.loads(
    (ROOT / "tests" / "fixtures" / "ha_mcp_9_1_0" / "dashboard" / "envelopes.json").read_text(
        encoding="utf-8"
    )
)
SECRET_URL = "http://ha-mcp:9583/synthetic-reviewed-secret/mcp"


def fixture_tool(object_pairs_hook=None):
    evidence = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"), object_pairs_hook=object_pairs_hook)
    return next(tool for tool in evidence["tools"] if tool.get("name") == REQUIRED_DASHBOARD_TOOL)


def published_runtime_tool():
    # The 9.2.0 capture is the published runtime descriptor itself.
    return fixture_tool()


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


def get_payload(url_path, config):
    payload = copy.deepcopy(ENVELOPES["get_stored_config"])
    payload.update(
        url_path=url_path,
        config=config,
        config_hash=_upstream_config_hash(config),
        config_size_bytes=len(json.dumps(config)),
    )
    return payload


def call_result(payload):
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "isError": False,
    }


class ReviewedFakeTransport:
    def __init__(self, handshake=None, payload=None):
        self.handshake = handshake or reviewed_handshake()
        self.payload = payload or {**ENVELOPES["list"], "dashboards": [], "count": 0}
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
        # HAMCP-437: 9.2.0 admits on its builtin attestation and the exact
        # release runtime contract; the RC3A 7.13.0 informational descriptor
        # fingerprints do not describe it.
        self.assertEqual(health["admission_status"], "admitted_builtin_attestation")
        self.assertEqual(health["attested_upstream_version"], REVIEWED_SERVER_VERSION)
        self.assertTrue(health["release_runtime_contract_match"])
        for key in (
            "input_contract_match",
            "security_contract_match",
            "output_contract_match",
            "runtime_contract_match",
        ):
            self.assertTrue(health[key], key)
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

    async def test_unattested_versions_fail_closed_before_contract_admission(self):
        for version in ("7.12.3", "7.13.1", "7.14.3", "8.0.1", "9.2.1"):
            with self.subTest(version=version):
                exc, health = await self._failure(version=version)
                self.assertEqual(
                    exc.code,
                    ErrorCode.UPSTREAM_DASHBOARD_VERSION_MISMATCH,
                )
                self.assertEqual(
                    health["admission_status"], "rejected_unknown_release"
                )
                self.assertEqual(
                    health["validation_reason"], "upstream_attestation_missing"
                )
                self.assertEqual(health["capability_status"], "unavailable")
                self.assertEqual(health["observed_upstream_version"], version)
                self.assertIsNone(health["admission_source"])
                self.assertIsNone(health["attestation_entry_id"])
                self.assertIsNone(health["attested_upstream_version"])
                self.assertIsNone(health["attested_source_commit"])
                self.assertIsNone(health["attested_image_index_digest"])
                self.assertEqual(health["revocation_status"], "not_evaluated")
                self.assertEqual(
                    health["runtime_descriptor_drift"], "not_comparable"
                )
                self.assertFalse(health["input_contract_match"])
                self.assertFalse(health["security_contract_match"])
                self.assertFalse(health["output_contract_match"])
                self.assertFalse(health["runtime_contract_match"])

    async def test_malformed_version_evidence_is_rejected(self):
        for version in ("release-latest", "", "7.14.2\nignore"):
            with self.subTest(version=version):
                exc, health = await self._failure(version=version)
                self.assertEqual(
                    exc.code,
                    ErrorCode.UPSTREAM_DASHBOARD_VERSION_MISMATCH,
                )
                self.assertEqual(
                    health["validation_reason"], "upstream_version_mismatch"
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
    # HAMCP-437 retired test_fixture_reproduces_known_runtime_schema_and_security_fingerprints
    # and test_published_artifact_delta_reproduces_observed_runtime_fingerprint:
    # their subject was the 7.13.0 fixture, its published-artifact delta and the
    # 7.13.0 fingerprint constants. The 9.2.0 builtin attestation is pinned in
    # test_rc2dev9_upstream_admission.ContractFamilyTests.

    def test_dictionary_and_property_order_do_not_change_fingerprints(self):
        reordered = fixture_tool(object_pairs_hook=lambda pairs: dict(reversed(pairs)))
        tool = fixture_tool()
        self.assertNotEqual(list(reordered), list(tool))
        self.assertEqual(_stable_hash(reordered), _stable_hash(tool))
        self.assertEqual(
            _stable_hash(_reviewed_security_contract_projection(reordered)),
            _stable_hash(_reviewed_security_contract_projection(tool)),
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
                self.assertTrue(health["security_contract_match"])
                self.assertTrue(health["release_runtime_contract_match"])
                self.assertEqual(health["capability_status"], "available")

    async def test_descriptive_and_presentation_drift_fails_closed_on_the_exact_release(self):
        # HAMCP-437: RC3A 7.13.0 admitted descriptive drift; on the exact 9.2.0
        # release the whole getter descriptor is a used-tool contract (HAMCP-319),
        # so the same drift fails closed before any read is dispatched.
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
                self.assertNotEqual(_stable_hash(tool), _stable_hash(fixture_tool()))
                self.assertEqual(
                    _stable_hash(_reviewed_security_contract_projection(tool)),
                    _stable_hash(_reviewed_security_contract_projection(fixture_tool())),
                )
                exc = await self._assert_contract_failure(
                    tool, "upstream_runtime_contract_mismatch"
                )
                self.assertEqual(
                    exc.code,
                    ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
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
                # HAMCP-437: on 9.2.0 the exact release runtime contract
                # refuses the drift first (RC3A 7.13.0 reported the input category).
                exc = await self._assert_contract_failure(
                    tool, "upstream_runtime_contract_mismatch"
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
            # HAMCP-437: 9.2.0 declares openWorldHint and readOnlyHint true.
            "open_world_false": {"openWorldHint": False},
            "read_only_false": {"readOnlyHint": False},
            "read_only_missing": {"readOnlyHint": None},
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
                # HAMCP-437: refused by the exact 9.2.0 runtime contract (RC3A
                # 7.13.0 reported the security category and annotation code).
                exc = await self._assert_contract_failure(
                    tool, "upstream_runtime_contract_mismatch"
                )
                self.assertEqual(
                    exc.code,
                    ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
                )

    async def test_output_contract_and_unknown_semantic_metadata_fail_closed(self):
        output = published_runtime_tool()
        output["outputSchema"] = {
            "type": "object",
            "properties": {"config_hash": {"type": "string"}},
        }
        # HAMCP-437: on 9.2.0 the exact runtime contract refuses it first.
        exc = await self._assert_contract_failure(
            output, "upstream_runtime_contract_mismatch"
        )
        self.assertEqual(
            exc.code,
            ErrorCode.UPSTREAM_DASHBOARD_REVIEWED_CONTRACT_MISMATCH,
        )

        metadata = published_runtime_tool()
        metadata["_meta"].setdefault("ha_mcp", {})["future_operation_semantics"] = "changed"
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

    async def test_read_only_false_is_contract_drift_and_fails_closed(self):
        # HAMCP-437: RC3A 7.13.0 declared readOnlyHint false, so true was the
        # drift; the reviewed 9.2.0 getter declares true.
        tool = fixture_tool()
        tool["annotations"]["readOnlyHint"] = False
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
            "upstream_runtime_contract_mismatch",
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
                    payload=get_payload("reviewed-dashboard", config)
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
        exact = BranchingTransport(payload=get_payload("reviewed-dashboard", config))
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
