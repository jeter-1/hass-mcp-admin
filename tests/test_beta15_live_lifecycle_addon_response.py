from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA))

from ha_mcp_engineering.clients.upstream_read import McpReadResult  # noqa: E402
from ha_mcp_engineering.audit import AuditLogger  # noqa: E402
from ha_mcp_engineering.governance.operational_lifecycle import (  # noqa: E402
    OperationalLifecycleGateway,
)
from ha_mcp_engineering.governance.service import (  # noqa: E402
    ChangeGovernanceService,
)
from ha_mcp_engineering.governance.storage import (  # noqa: E402
    ChangePlanRepository,
)
from ha_mcp_engineering.errors import ErrorCode, GovernanceError  # noqa: E402
from ha_mcp_engineering.providers import operational_lifecycle as lifecycle  # noqa: E402
from ha_mcp_engineering.providers.operational_lifecycle import (  # noqa: E402
    LIFECYCLE_ADDON_RESPONSE_ENVELOPE_TEXT,
    LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED,
    LIFECYCLE_ADDON_RESPONSE_MODEL_TEXT_V1,
    LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1,
    OperationalLifecycleProviderError,
    ReviewedOperationalLifecycleProvider,
)
from ha_mcp_engineering.providers.supervisor_self import (  # noqa: E402
    SupervisorSelfAddonIdentity,
)
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    catalog_fingerprint,
)
from ha_mcp_engineering.version import SERVER_VERSION  # noqa: E402
from tests.test_2_1a_beta2_operational_lifecycle import (  # noqa: E402
    FakeMcpTransport,
    LegacyGateway,
    UPSTREAM_ADDON_SLUG,
    UPSTREAM_ADDON_NAME,
    lifecycle_settings,
    provider_evidence,
    upstream_addon_identity,
)


SOURCE_DERIVED_MINIMUM_DETAIL_BYTES = 71_986


def live_equivalent_payload() -> dict:
    """Return a secret-free detail shaped like Supervisor 2026.7.4.

    Exact ha-mcp 8.0.0 passes Supervisor ``/addons/{slug}/info`` through
    without projection.  Its seven public translation documents make the
    compact FastMCP text representation at least 71,986 bytes when the
    required identity fields are included. The synthetic filler preserves that
    source-derived lower bound without copying production options or
    configuration.
    """

    addon = {
        "slug": UPSTREAM_ADDON_SLUG,
        "name": "Home Assistant MCP Server",
        "version": "8.0.0",
        "state": "started",
        "repository": "abcdef12",
        "update_available": False,
        "translations": {
            locale: {"configuration": {"synthetic_contract_text": ""}}
            for locale in ("de", "en", "es", "fr", "it", "ru", "zh-Hans")
        },
    }
    payload = {"success": True, "addon": addon}
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    padding = SOURCE_DERIVED_MINIMUM_DETAIL_BYTES - len(
        encoded.encode("utf-8")
    )
    if padding <= 0:
        raise AssertionError("live-equivalent fixture baseline exceeded target")
    addon["translations"]["en"]["configuration"][
        "synthetic_contract_text"
    ] = "x" * padding
    assert len(
        json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ) == SOURCE_DERIVED_MINIMUM_DETAIL_BYTES
    return payload


def call_result(payload: dict) -> dict:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    payload,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            }
        ],
        "structuredContent": deepcopy(payload),
        "isError": False,
    }


def installed_summary(addons: list[dict]) -> dict[str, int]:
    return {
        "total_installed": len(addons),
        "running": sum(1 for addon in addons if addon["state"] == "started"),
        "stopped": sum(1 for addon in addons if addon["state"] != "started"),
        "updates_available": sum(
            1 for addon in addons if addon.get("update_available") is True
        ),
    }


class DetailTransport(FakeMcpTransport):
    def __init__(
        self,
        detail_result: dict | None = None,
        *,
        inventory_result: dict | None = None,
        version: str = "8.0.0",
    ) -> None:
        super().__init__(version)
        self.detail_result = detail_result or call_result(
            live_equivalent_payload()
        )
        self.inventory_result = inventory_result

    async def execute_read(self, tool_name, arguments, **kwargs):
        result = await super().execute_read(tool_name, arguments, **kwargs)
        if (
            tool_name == "ha_get_addon"
            and arguments.get("source") == "installed"
            and self.inventory_result is not None
        ):
            return replace(
                result,
                call_result=deepcopy(self.inventory_result),
            )
        if tool_name == "ha_get_addon" and "slug" in arguments:
            return replace(
                result,
                call_result=deepcopy(self.detail_result),
            )
        return result


def configured_provider(
    transport: FakeMcpTransport,
) -> ReviewedOperationalLifecycleProvider:
    provider = ReviewedOperationalLifecycleProvider()
    provider.configure(
        lifecycle_settings(
            "http://abcdef12-ha-mcp:9583/synthetic-upstream-secret/mcp"
        ),
        transport=transport,
    )
    return provider


def planning_gateway(
    provider: ReviewedOperationalLifecycleProvider,
) -> OperationalLifecycleGateway:
    async def self_identity():
        return SupervisorSelfAddonIdentity(
            slug="df26dea6_hass_mcp_engineering_beta",
            name="HA MCP Engineering Server Beta",
            version=SERVER_VERSION,
            repository="df26dea6",
        )

    return OperationalLifecycleGateway(
        provider,
        None,
        None,
        configuration_validator=lambda: None,
        runtime_snapshot=lambda: {
            "upstream_version": "8.0.0",
            "upstream_protocol": "2025-03-26",
            "upstream_catalog_fingerprint": (
                "c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768"
            ),
            "upstream_admission_status": "admitted_exact",
            "fallback_count": 0,
        },
        process_instance_id="beta15-live-equivalent-planning",
        self_addon_identity_resolver=self_identity,
    )


class LiveLifecycleAddonResponseTests(unittest.IsolatedAsyncioTestCase):
    def test_immutable_addon_job_uses_live_equivalent_detail_profile(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        fixture = (
            ROOT / "scripts/fake_ha_read_gateway_contract_server.py"
        ).read_text(encoding="utf-8")
        acceptance = (
            ROOT / "scripts/exact_addon_runtime_acceptance.py"
        ).read_text(encoding="utf-8")

        self.assertIn("addon_detail_profile: live-8.0.0", workflow)
        self.assertIn("addon_detail_profile: live-8.1.0", workflow)
        self.assertIn(
            '--addon-detail-profile "$ADDON_DETAIL_PROFILE"', workflow
        )
        self.assertIn(
            "sha256:693ecd5c68f98e64111fbf58e02547a51b2168a942056684dbe262c550aff9cd",
            workflow,
        )
        self.assertIn("SOURCE_DERIVED_MINIMUM_ADDON_DETAIL_BYTES", fixture)
        self.assertIn('ADDON_DETAIL_PROFILE = "compact"', fixture)
        self.assertIn(
            "EXPECTED_SOURCE_DERIVED_MINIMUM_DETAIL_BYTES",
            acceptance,
        )
        self.assertIn(
            "EXPECTED_LIFECYCLE_ADDON_RESPONSE_MODEL",
            acceptance,
        )
        self.assertIn("immutable_addon_detail_text_bytes", acceptance)
        self.assertIn("detail_structured_content_present", acceptance)

    def test_known_response_mismatch_maps_to_contract_error(self):
        self.assertEqual(
            ChangeGovernanceService._lifecycle_error_code(
                "addon_response_contract_mismatch",
                dispatched=False,
            ),
            ErrorCode.OPERATIONAL_CONTRACT_MISMATCH,
        )

    async def test_beta14_text_limit_reproduces_then_exact_model_accepts(self):
        payload = live_equivalent_payload()
        result = call_result(payload)
        provider = configured_provider(DetailTransport(result))

        with self.assertRaises(OperationalLifecycleProviderError) as caught:
            provider._decode(result, dispatched=False)
        self.assertEqual(caught.exception.category, "invalid_response")
        self.assertFalse(caught.exception.dispatched)

        addon = await provider.get_addon(UPSTREAM_ADDON_SLUG)

        self.assertEqual(addon["slug"], UPSTREAM_ADDON_SLUG)
        self.assertEqual(addon["version"], "8.0.0")
        self.assertNotIn("translations", addon)
        evidence = addon["provider"]
        self.assertEqual(
            evidence["lifecycle_addon_response_contract_model"],
            LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1,
        )
        self.assertEqual(
            evidence["lifecycle_addon_response_envelope_variant"],
            LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED,
        )
        health = provider.health_snapshot()
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)
        self.assertIsNone(health["lifecycle_addon_response_diagnostics"])

    async def test_live_equivalent_response_persists_proposal_without_dispatch(
        self,
    ):
        sensitive_sentinel = "beta15-sensitive-upstream-sentinel"
        credential_url = "https://user:secret@example.invalid/private"
        payload = live_equivalent_payload()
        payload["addon"]["options"] = {
            "upstream_secret": sensitive_sentinel,
        }
        payload["addon"]["arbitrary_debug_url"] = credential_url
        transport = DetailTransport(call_result(payload))
        transport.addons[0]["name"] = sensitive_sentinel
        provider = configured_provider(transport)
        gateway = planning_gateway(provider)
        with tempfile.TemporaryDirectory() as directory:
            repository = ChangePlanRepository(Path(directory) / "plans")
            service = ChangeGovernanceService(
                repository,
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=gateway,
            )

            created = await service.create_addon_restart_plan(
                addon_slug=UPSTREAM_ADDON_SLUG,
                expiration_minutes=5,
            )

            persisted = repository.list()
        self.assertEqual(len(persisted), 1)
        self.assertTrue(created["proposal_only"])
        self.assertFalse(created["provider_dispatch_occurred"])
        self.assertNotIn(
            "ha_manage_addon",
            [tool_name for tool_name, _arguments in transport.calls],
        )
        health = provider.health_snapshot()
        retained = json.dumps(
            {
                "created": created,
                "persisted": [plan.to_dict() for plan in persisted],
                "health": health,
            },
            sort_keys=True,
        )
        self.assertNotIn(sensitive_sentinel, retained)
        self.assertNotIn(credential_url, retained)
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_beta14_response_path_failed_before_plan_persistence(self):
        result = call_result(live_equivalent_payload())
        transport = DetailTransport(result)
        provider = configured_provider(transport)
        gateway = planning_gateway(provider)
        legacy_contract = lifecycle.LifecycleAddonResponseContract(
            model=LIFECYCLE_ADDON_RESPONSE_MODEL_TEXT_V1,
            envelope_variant=LIFECYCLE_ADDON_RESPONSE_ENVELOPE_TEXT,
        )
        key = (
            "ha-mcp-v8.0.0-d65630f6",
            "8.0.0",
            "2025-03-26",
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = ChangePlanRepository(Path(directory) / "plans")
            service = ChangeGovernanceService(
                repository,
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=gateway,
            )

            with patch.dict(
                lifecycle._LIFECYCLE_ADDON_RESPONSE_CONTRACTS,
                {key: legacy_contract},
            ):
                with self.assertRaises(GovernanceError) as caught:
                    await service.create_addon_restart_plan(
                        addon_slug=UPSTREAM_ADDON_SLUG,
                        expiration_minutes=5,
                    )

            self.assertEqual(repository.list(), [])
        self.assertEqual(
            caught.exception.code,
            ErrorCode.OPERATIONAL_PROVIDER_UNAVAILABLE,
        )
        health = provider.health_snapshot()
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_response_mismatch_creates_no_plan(self):
        result = call_result(live_equivalent_payload())
        result.pop("structuredContent")
        transport = DetailTransport(result)
        provider = configured_provider(transport)
        with tempfile.TemporaryDirectory() as directory:
            repository = ChangePlanRepository(Path(directory) / "plans")
            service = ChangeGovernanceService(
                repository,
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=planning_gateway(provider),
            )

            with self.assertRaises(GovernanceError) as caught:
                await service.create_addon_restart_plan(
                    addon_slug=UPSTREAM_ADDON_SLUG,
                    expiration_minutes=5,
                )

            self.assertEqual(repository.list(), [])
        self.assertEqual(
            caught.exception.code,
            ErrorCode.OPERATIONAL_CONTRACT_MISMATCH,
        )
        self.assertNotIn(
            "ha_manage_addon",
            [tool_name for tool_name, _arguments in transport.calls],
        )
        health = provider.health_snapshot()
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_existing_compact_and_7_14_2_models_remain_accepted(self):
        for version in ("7.14.2", "8.0.0"):
            with self.subTest(version=version):
                transport = FakeMcpTransport(version)
                provider = configured_provider(transport)

                addon = await provider.get_addon(UPSTREAM_ADDON_SLUG)

                self.assertEqual(addon["version"], version)
                health = provider.health_snapshot()
                expected_model = (
                    LIFECYCLE_ADDON_RESPONSE_MODEL_TEXT_V1
                    if version == "7.14.2"
                    else LIFECYCLE_ADDON_RESPONSE_MODEL_STRUCTURED_V1
                )
                expected_envelope = (
                    LIFECYCLE_ADDON_RESPONSE_ENVELOPE_TEXT
                    if version == "7.14.2"
                    else LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED
                )
                self.assertEqual(
                    health["lifecycle_addon_response_contract_model"],
                    expected_model,
                )
                self.assertEqual(
                    health["lifecycle_addon_response_envelope_variant"],
                    expected_envelope,
                )
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)

    async def test_unknown_response_contract_model_fails_closed(self):
        key = (
            "ha-mcp-v8.0.0-d65630f6",
            "8.0.0",
            "2025-03-26",
        )
        unsupported = lifecycle.LifecycleAddonResponseContract(
            model="unreviewed-lifecycle-response-model",
            envelope_variant=LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED,
        )
        provider = configured_provider(DetailTransport())
        with patch.dict(
            lifecycle._LIFECYCLE_ADDON_RESPONSE_CONTRACTS,
            {key: unsupported},
        ):
            with self.assertRaises(
                OperationalLifecycleProviderError
            ) as caught:
                await provider.probe("restart_addon")

        self.assertEqual(
            caught.exception.category,
            "unsupported_response_contract_model",
        )
        self.assertFalse(caught.exception.dispatched)
        health = provider.health_snapshot()
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_malformed_structured_envelopes_fail_before_dispatch(self):
        base = call_result(live_equivalent_payload())
        cases: dict[str, tuple[object, str]] = {
            "outer_not_mapping": (
                ["untrusted"],
                "addon_response_contract_mismatch",
            ),
        }

        missing_structured = deepcopy(base)
        missing_structured.pop("structuredContent")
        cases["missing_structured"] = (
            missing_structured,
            "addon_response_contract_mismatch",
        )

        missing_content = deepcopy(base)
        missing_content.pop("content")
        cases["missing_content"] = (
            missing_content,
            "addon_response_contract_mismatch",
        )

        malformed_content = deepcopy(base)
        malformed_content["content"] = []
        cases["malformed_content"] = (
            malformed_content,
            "addon_response_contract_mismatch",
        )

        content_mapping = deepcopy(base)
        content_mapping["content"] = {"type": "text", "text": "{}"}
        cases["content_not_list"] = (
            content_mapping,
            "addon_response_contract_mismatch",
        )

        multiple_items = deepcopy(base)
        multiple_items["content"].append(
            deepcopy(multiple_items["content"][0])
        )
        cases["multiple_content_items"] = (
            multiple_items,
            "addon_response_contract_mismatch",
        )

        non_mapping_item = deepcopy(base)
        non_mapping_item["content"] = ["untrusted"]
        cases["content_item_not_mapping"] = (
            non_mapping_item,
            "addon_response_contract_mismatch",
        )

        wrong_item_type = deepcopy(base)
        wrong_item_type["content"][0]["type"] = "image"
        cases["content_item_wrong_type"] = (
            wrong_item_type,
            "addon_response_contract_mismatch",
        )

        missing_item_type = deepcopy(base)
        missing_item_type["content"][0].pop("type")
        cases["content_item_missing_type"] = (
            missing_item_type,
            "addon_response_contract_mismatch",
        )

        missing_item_text = deepcopy(base)
        missing_item_text["content"][0].pop("text")
        cases["content_item_missing_text"] = (
            missing_item_text,
            "addon_response_contract_mismatch",
        )

        non_string_text = deepcopy(base)
        non_string_text["content"][0]["text"] = {"success": True}
        cases["content_text_not_string"] = (
            non_string_text,
            "addon_response_contract_mismatch",
        )

        structured_not_mapping = deepcopy(base)
        structured_not_mapping["structuredContent"] = []
        cases["structured_content_not_mapping"] = (
            structured_not_mapping,
            "addon_response_contract_mismatch",
        )

        missing_success = deepcopy(base)
        missing_success["structuredContent"].pop("success")
        missing_success["content"][0]["text"] = json.dumps(
            missing_success["structuredContent"],
            separators=(",", ":"),
        )
        cases["structured_content_missing_success"] = (
            missing_success,
            "addon_response_contract_mismatch",
        )

        success_false = deepcopy(base)
        success_false["structuredContent"] = {
            "success": False,
            "error": {"code": "SERVICE_CALL_FAILED"},
        }
        success_false["content"][0]["text"] = json.dumps(
            success_false["structuredContent"],
            separators=(",", ":"),
        )
        success_false["isError"] = True
        cases["success_false"] = (success_false, "operation_failed")

        inconsistent = deepcopy(base)
        inconsistent["structuredContent"]["addon"]["name"] = (
            "Conflicting structured name"
        )
        cases["inconsistent_parallel_envelopes"] = (
            inconsistent,
            "addon_response_contract_mismatch",
        )

        type_divergent_success = deepcopy(base)
        text_success = deepcopy(type_divergent_success["structuredContent"])
        text_success["success"] = 1
        type_divergent_success["content"][0]["text"] = json.dumps(
            text_success,
            separators=(",", ":"),
        )
        cases["boolean_number_success_divergence"] = (
            type_divergent_success,
            "addon_response_contract_mismatch",
        )

        type_divergent_update = deepcopy(base)
        text_update = deepcopy(type_divergent_update["structuredContent"])
        text_update["addon"]["update_available"] = 0
        type_divergent_update["content"][0]["text"] = json.dumps(
            text_update,
            separators=(",", ":"),
        )
        cases["boolean_number_identity_divergence"] = (
            type_divergent_update,
            "addon_response_contract_mismatch",
        )

        null_omitted_update = deepcopy(base)
        text_update = deepcopy(null_omitted_update["structuredContent"])
        text_update["addon"].pop("update_available")
        null_omitted_update["structuredContent"]["addon"][
            "update_available"
        ] = None
        null_omitted_update["content"][0]["text"] = json.dumps(
            text_update,
            separators=(",", ":"),
        )
        cases["null_omitted_identity_envelope_divergence"] = (
            null_omitted_update,
            "addon_response_contract_mismatch",
        )

        oversized = deepcopy(base)
        oversized["content"][0]["text"] = "x" * 250_001
        cases["model_bound_exceeded"] = (
            oversized,
            "addon_response_contract_mismatch",
        )

        warning = deepcopy(base)
        warning["structuredContent"]["warnings"] = [
            {"code": "INCOMPLETE_INVENTORY"}
        ]
        warning["content"][0]["text"] = json.dumps(
            warning["structuredContent"],
            separators=(",", ":"),
        )
        cases["incomplete_warning"] = (
            warning,
            "addon_response_contract_mismatch",
        )

        wrong_truncation_type = deepcopy(base)
        wrong_truncation_type["structuredContent"]["truncated"] = 1
        wrong_truncation_type["content"][0]["text"] = json.dumps(
            wrong_truncation_type["structuredContent"],
            separators=(",", ":"),
        )
        cases["wrong_truncation_marker_type"] = (
            wrong_truncation_type,
            "addon_response_contract_mismatch",
        )

        for marker in ("partial", "truncated"):
            incomplete = deepcopy(base)
            incomplete["structuredContent"][marker] = True
            incomplete["content"][0]["text"] = json.dumps(
                incomplete["structuredContent"],
                separators=(",", ":"),
            )
            cases[f"explicit_{marker}"] = (
                incomplete,
                "addon_response_contract_mismatch",
            )

        for name, (result, category) in cases.items():
            with self.subTest(name=name):
                provider = configured_provider(DetailTransport(result))
                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as caught:
                    await provider.get_addon(UPSTREAM_ADDON_SLUG)
                self.assertEqual(caught.exception.category, category)
                self.assertFalse(caught.exception.dispatched)
                health = provider.health_snapshot()
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)
                diagnostics = health["lifecycle_addon_response_diagnostics"]
                expected_diagnostics = {
                    "missing_structured": (["/structuredContent"], []),
                    "structured_content_not_mapping": (
                        [],
                        ["/structuredContent"],
                    ),
                    "missing_content": (["/content"], []),
                    "content_item_missing_type": (
                        ["/content/0/type"],
                        [],
                    ),
                    "content_item_missing_text": (
                        ["/content/0/text"],
                        [],
                    ),
                    "content_item_wrong_type": (
                        [],
                        ["/content/0/type"],
                    ),
                    "content_text_not_string": (
                        [],
                        ["/content/0/text"],
                    ),
                    "structured_content_missing_success": (
                        ["/structuredContent/success"],
                        [],
                    ),
                }
                if name in expected_diagnostics:
                    expected_missing, expected_invalid = expected_diagnostics[
                        name
                    ]
                    self.assertEqual(
                        diagnostics["missing_semantic_field_paths"],
                        expected_missing,
                    )
                    self.assertEqual(
                        diagnostics["invalid_semantic_field_paths"],
                        expected_invalid,
                    )
    async def test_inventory_completeness_and_cardinality_fail_closed(self):
        seed = DetailTransport()
        duplicate_addons = deepcopy(seed.addons)
        duplicate_addons.append(deepcopy(duplicate_addons[-1]))
        missing_addons = [
            item
            for item in seed.addons
            if item["slug"] != UPSTREAM_ADDON_SLUG
        ]
        cases = {
            "missing_addons": {
                "summary": installed_summary([]),
            },
            "missing_summary": {
                "addons": deepcopy(seed.addons),
            },
            "wrong_collection": {
                "addons": {},
                "summary": installed_summary([]),
            },
            "wrong_summary_type": {
                "addons": deepcopy(seed.addons),
                "summary": [],
            },
            "summary_mismatch": {
                "addons": deepcopy(seed.addons),
                "summary": {
                    **installed_summary(seed.addons),
                    "total_installed": 99,
                },
            },
            "duplicate_match": {
                "addons": duplicate_addons,
                "summary": installed_summary(duplicate_addons),
            },
            "truncated": {
                "addons": deepcopy(seed.addons),
                "summary": installed_summary(seed.addons),
                "truncated": True,
            },
            "warning": {
                "addons": deepcopy(seed.addons),
                "summary": installed_summary(seed.addons),
                "warnings": [{"code": "INCOMPLETE_INVENTORY"}],
            },
            "wrong_partial_type": {
                "addons": deepcopy(seed.addons),
                "summary": installed_summary(seed.addons),
                "partial": 0,
            },
            "wrong_pagination_type": {
                "addons": deepcopy(seed.addons),
                "summary": installed_summary(seed.addons),
                "pagination": {"has_more": 0},
            },
            "unknown_pagination_cursor": {
                "addons": deepcopy(seed.addons),
                "summary": installed_summary(seed.addons),
                "pagination": {"cursor": "synthetic-more"},
            },
            "top_level_next_cursor": {
                "addons": deepcopy(seed.addons),
                "summary": installed_summary(seed.addons),
                "next_cursor": "synthetic-more",
            },
            "summary_truncated": {
                "addons": deepcopy(seed.addons),
                "summary": {
                    **installed_summary(seed.addons),
                    "truncated": True,
                },
            },
            "summary_next_cursor": {
                "addons": deepcopy(seed.addons),
                "summary": {
                    **installed_summary(seed.addons),
                    "next_cursor": "synthetic-more",
                },
            },
        }
        for summary_field in (
            "total_installed",
            "running",
            "stopped",
            "updates_available",
        ):
            missing_summary = installed_summary(seed.addons)
            missing_summary.pop(summary_field)
            cases[f"missing_summary_{summary_field}"] = {
                "addons": deepcopy(seed.addons),
                "summary": missing_summary,
            }
            wrong_summary = installed_summary(seed.addons)
            wrong_summary[summary_field] = True
            cases[f"wrong_summary_type_{summary_field}"] = {
                "addons": deepcopy(seed.addons),
                "summary": wrong_summary,
            }
            incoherent_summary = installed_summary(seed.addons)
            incoherent_summary[summary_field] += 1
            cases[f"incoherent_summary_{summary_field}"] = {
                "addons": deepcopy(seed.addons),
                "summary": incoherent_summary,
            }
        for name, field, value in (
            ("inventory_not_installed", "installed", False),
            ("inventory_wrong_state", "state", "unknown"),
            ("inventory_wrong_repository", "repository", "ffffffff"),
            ("inventory_wrong_update_type", "update_available", 0),
        ):
            addons = deepcopy(seed.addons)
            addons[-1][field] = value
            cases[name] = {
                "addons": addons,
                "summary": installed_summary(seed.addons),
            }
        missing_installed = deepcopy(seed.addons)
        missing_installed[-1].pop("installed")
        cases["inventory_missing_installed"] = {
            "addons": missing_installed,
            "summary": installed_summary(seed.addons),
        }
        missing_state = deepcopy(seed.addons)
        missing_state[-1].pop("state")
        cases["inventory_missing_state"] = {
            "addons": missing_state,
            "summary": installed_summary(seed.addons),
        }
        non_mapping_item = deepcopy(seed.addons)
        non_mapping_item[-1] = "untrusted"
        cases["inventory_item_not_mapping"] = {
            "addons": non_mapping_item,
            "summary": installed_summary(seed.addons),
        }
        for field, value in (
            ("slug", 7),
            ("name", False),
            ("version", []),
            ("repository", {}),
        ):
            wrong_field_type = deepcopy(seed.addons)
            wrong_field_type[-1][field] = value
            cases[f"inventory_wrong_{field}_type"] = {
                "addons": wrong_field_type,
                "summary": installed_summary(seed.addons),
            }
        for name, updates in cases.items():
            with self.subTest(name=name):
                transport = DetailTransport()
                transport.inventory_result = call_result(
                    {"success": True, **updates}
                )
                provider = configured_provider(transport)
                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as caught:
                    await provider.get_addon(UPSTREAM_ADDON_SLUG)
                self.assertEqual(
                    caught.exception.category,
                    "addon_response_contract_mismatch",
                )
                self.assertFalse(caught.exception.dispatched)
                self.assertNotIn(
                    "ha_manage_addon",
                    [tool_name for tool_name, _ in transport.calls],
                )
                health = provider.health_snapshot()
                self.assertEqual(
                    sum(health["dispatch_counts"].values()),
                    0,
                )
                self.assertEqual(health["fallback_count"], 0)
                diagnostics = health["lifecycle_addon_response_diagnostics"]
                expected_diagnostics = {
                    "missing_addons": (["/addons"], []),
                    "missing_summary": (["/summary"], []),
                    "wrong_collection": ([], ["/addons"]),
                    "wrong_summary_type": ([], ["/summary"]),
                }
                if name in expected_diagnostics:
                    expected_missing, expected_invalid = expected_diagnostics[
                        name
                    ]
                    self.assertEqual(
                        diagnostics["missing_semantic_field_paths"],
                        expected_missing,
                    )
                    self.assertEqual(
                        diagnostics["invalid_semantic_field_paths"],
                        expected_invalid,
                    )
                if name.startswith("missing_summary_"):
                    summary_field = name.removeprefix("missing_summary_")
                    self.assertIn(
                        f"/summary/{summary_field}",
                        diagnostics["missing_semantic_field_paths"],
                    )
                    self.assertNotIn(
                        f"/summary/{summary_field}",
                        diagnostics["invalid_semantic_field_paths"],
                    )
                expected_marker_path = {
                    "truncated": "/structuredContent/truncated",
                    "warning": "/structuredContent/warnings",
                    "top_level_next_cursor": (
                        "/structuredContent/next_cursor"
                    ),
                    "summary_truncated": "/summary/truncated",
                    "summary_next_cursor": "/summary/next_cursor",
                }.get(name)
                if expected_marker_path is not None:
                    self.assertIn(
                        expected_marker_path,
                        diagnostics["invalid_semantic_field_paths"],
                    )

        valid_inventory = {
            "success": True,
            "addons": deepcopy(seed.addons),
            "summary": installed_summary(seed.addons),
        }
        missing_structured = call_result(valid_inventory)
        missing_structured.pop("structuredContent")
        divergent_structured = call_result(valid_inventory)
        divergent_structured["structuredContent"] = {
            "success": True,
            "addons": [],
            "summary": installed_summary([]),
        }
        for name, result in (
            ("inventory_missing_structured", missing_structured),
            ("inventory_divergent_structured", divergent_structured),
        ):
            with self.subTest(name=name):
                provider = configured_provider(
                    DetailTransport(inventory_result=result)
                )
                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as caught:
                    await provider.get_addon(UPSTREAM_ADDON_SLUG)
                self.assertEqual(
                    caught.exception.category,
                    "addon_response_contract_mismatch",
                )
                self.assertFalse(caught.exception.dispatched)
                health = provider.health_snapshot()
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)

        missing_transport = DetailTransport()
        missing_transport.inventory_result = call_result(
            {
                "success": True,
                "addons": missing_addons,
                "summary": installed_summary(missing_addons),
            }
        )
        provider = configured_provider(missing_transport)
        with self.assertRaises(OperationalLifecycleProviderError) as caught:
            await provider.get_addon(UPSTREAM_ADDON_SLUG)
        self.assertEqual(caught.exception.category, "addon_not_found")
        self.assertFalse(caught.exception.dispatched)
        health = provider.health_snapshot()
        self.assertEqual(
            sum(health["dispatch_counts"].values()),
            0,
        )
        self.assertEqual(health["fallback_count"], 0)

        missing_detail = DetailTransport(
            call_result({"success": True})
        )
        provider = configured_provider(missing_detail)
        with self.assertRaises(OperationalLifecycleProviderError) as caught:
            await provider.get_addon(UPSTREAM_ADDON_SLUG)
        self.assertEqual(
            caught.exception.category,
            "addon_response_contract_mismatch",
        )
        self.assertFalse(caught.exception.dispatched)
        health = provider.health_snapshot()
        diagnostics = health["lifecycle_addon_response_diagnostics"]
        self.assertEqual(
            diagnostics["missing_semantic_field_paths"],
            ["/addon"],
        )
        self.assertEqual(
            diagnostics["invalid_semantic_field_paths"],
            [],
        )
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_text_model_requires_exact_boolean_success(self):
        for value in (None, 1, "true"):
            with self.subTest(value=value):
                payload = {
                    "addon": {
                        "slug": UPSTREAM_ADDON_SLUG,
                        "name": "Home Assistant MCP Server",
                        "version": "7.14.2",
                        "state": "started",
                        "repository": "abcdef12",
                        "update_available": False,
                    }
                }
                if value is not None:
                    payload["success"] = value
                provider = configured_provider(
                    DetailTransport(
                        call_result(payload),
                        version="7.14.2",
                    )
                )

                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as caught:
                    await provider.get_addon(UPSTREAM_ADDON_SLUG)

                self.assertEqual(
                    caught.exception.category,
                    "addon_response_contract_mismatch",
                )
                self.assertFalse(caught.exception.dispatched)
                health = provider.health_snapshot()
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)

    async def test_optional_detail_update_flag_normalizes_null_and_omitted(
        self,
    ):
        projected = []
        for inventory_variant in ("null", "omitted"):
            for detail_variant in ("null", "omitted"):
                with self.subTest(
                    inventory=inventory_variant,
                    detail=detail_variant,
                ):
                    transport = DetailTransport()
                    payload = live_equivalent_payload()
                    inventory_addon = next(
                        addon
                        for addon in transport.addons
                        if addon["slug"] == UPSTREAM_ADDON_SLUG
                    )
                    if inventory_variant == "null":
                        inventory_addon["update_available"] = None
                    else:
                        inventory_addon.pop("update_available")
                    if detail_variant == "null":
                        payload["addon"]["update_available"] = None
                    else:
                        payload["addon"].pop("update_available")
                    transport.detail_result = call_result(payload)
                    addon = lifecycle._project_addon_identity(
                        payload["addon"],
                        required_slug=UPSTREAM_ADDON_SLUG,
                    )
                    self.assertIsNotNone(addon)
                    projected.append(addon)
                    provider = configured_provider(transport)
                    accepted = await provider.get_addon(
                        UPSTREAM_ADDON_SLUG
                    )
                    self.assertIsNone(accepted["update_available"])
                    health = provider.health_snapshot()
                    self.assertEqual(
                        sum(health["dispatch_counts"].values()),
                        0,
                    )
                    self.assertEqual(health["fallback_count"], 0)
        self.assertTrue(all(item == projected[0] for item in projected[1:]))
        self.assertIsNone(projected[0]["update_available"])

    async def test_optional_repository_normalizes_null_and_omitted(self):
        projected = []
        for inventory_variant in ("null", "omitted"):
            for detail_variant in ("null", "omitted"):
                with self.subTest(
                    inventory=inventory_variant,
                    detail=detail_variant,
                ):
                    transport = DetailTransport()
                    payload = live_equivalent_payload()
                    inventory_addon = next(
                        addon
                        for addon in transport.addons
                        if addon["slug"] == UPSTREAM_ADDON_SLUG
                    )
                    if inventory_variant == "null":
                        inventory_addon["repository"] = None
                    else:
                        inventory_addon.pop("repository")
                    if detail_variant == "null":
                        payload["addon"]["repository"] = None
                    else:
                        payload["addon"].pop("repository")
                    transport.detail_result = call_result(payload)
                    provider = configured_provider(transport)

                    accepted = await provider.get_addon(
                        UPSTREAM_ADDON_SLUG
                    )

                    projected.append(accepted)
                    self.assertIsNone(accepted["repository"])
                    health = provider.health_snapshot()
                    self.assertEqual(
                        sum(health["dispatch_counts"].values()),
                        0,
                    )
                    self.assertEqual(health["fallback_count"], 0)
        self.assertTrue(
            all(
                item["repository"] == projected[0]["repository"]
                for item in projected[1:]
            )
        )

    async def test_inventory_error_is_not_recorded_as_addon_not_found(self):
        payload = {
            "success": False,
            "error": {"code": "RESOURCE_NOT_FOUND"},
        }
        provider = configured_provider(
            DetailTransport(inventory_result=call_result(payload))
        )

        with self.assertRaises(OperationalLifecycleProviderError) as caught:
            await provider.get_addon(UPSTREAM_ADDON_SLUG)

        self.assertEqual(caught.exception.category, "resource_not_found")
        self.assertFalse(caught.exception.dispatched)
        health = provider.health_snapshot()
        self.assertNotEqual(health["operational_status"], "available")
        self.assertEqual(health["failure_counts"]["resource_not_found"], 1)
        self.assertEqual(
            health["domain_outcome_counts"].get("addon_not_found", 0),
            0,
        )
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_response_contract_failures_are_conclusive_after_dispatch(
        self,
    ):
        categories = (
            "addon_response_contract_mismatch",
            "unsupported_response_contract_model",
        )
        baseline = {
            "addon": {
                "slug": UPSTREAM_ADDON_SLUG,
                "name": UPSTREAM_ADDON_NAME,
                "version": "7.14.2",
                "state": "started",
            },
            "target_class": "other_addon",
        }
        for category in categories:
            with self.subTest(stage="readback", category=category):
                class ReadbackFailureProvider:
                    async def get_addon(self, _slug):
                        raise OperationalLifecycleProviderError(
                            category,
                            dispatched=False,
                        )

                gateway = OperationalLifecycleGateway(
                    ReadbackFailureProvider(),
                    None,
                    None,
                    configuration_validator=lambda: None,
                    runtime_snapshot=lambda: {},
                    process_instance_id="beta15-readback-failure",
                )
                result = await gateway.verify_addon_restart(
                    UPSTREAM_ADDON_SLUG,
                    baseline=baseline,
                    provider_response_received=True,
                    provider_evidence={},
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(
                    result["evidence"]["failure_category"],
                    category,
                )
                self.assertFalse(
                    result["evidence"]["redispatch_performed"]
                )

        runtime = {
            "upstream_version": "7.14.2",
            "upstream_protocol": "2025-03-26",
            "upstream_catalog_fingerprint": (
                "c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c"
            ),
            "upstream_admission_status": "admitted_exact",
            "fallback_count": 0,
        }
        upstream_baseline = {
            **baseline,
            "target_class": "upstream_ha_mcp_addon",
            "runtime": deepcopy(runtime),
            "upstream_addon_identity": upstream_addon_identity(),
        }
        for category in categories:
            with self.subTest(stage="readmission", category=category):
                class ReadmissionFailureProvider:
                    async def get_addon(self, slug):
                        return {
                            "slug": slug,
                            "name": UPSTREAM_ADDON_NAME,
                            "version": "7.14.2",
                            "state": "started",
                            "repository": "abcdef12",
                            "upstream_addon_identity": (
                                upstream_addon_identity()
                            ),
                        }

                    async def probe(self, _operation):
                        raise OperationalLifecycleProviderError(
                            category,
                            dispatched=False,
                        )

                gateway = OperationalLifecycleGateway(
                    ReadmissionFailureProvider(),
                    None,
                    None,
                    configuration_validator=lambda: None,
                    runtime_snapshot=lambda: deepcopy(runtime),
                    process_instance_id="beta15-readmission-failure",
                )
                result = await gateway.verify_addon_restart(
                    UPSTREAM_ADDON_SLUG,
                    baseline=upstream_baseline,
                    provider_response_received=True,
                    provider_evidence=provider_evidence("restart_addon"),
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(
                    result["evidence"]["failure_category"],
                    category,
                )
                self.assertFalse(
                    result["evidence"]["redispatch_performed"]
                )

    async def test_sensitive_identity_is_never_projected(self):
        cases = (
            (
                "name_absolute_credentials",
                "name",
                "https://user:secret@example.invalid/name",
            ),
            (
                "name_protocol_relative_credentials",
                "name",
                "//user:secret@example.invalid/name",
            ),
            (
                "name_token_shape",
                "name",
                "ghp_" + "A" * 36,
            ),
            (
                "name_embedded_token_shape",
                "name",
                "Synthetic add-on credential ghp_" + "A" * 36,
            ),
            (
                "name_embedded_protocol_relative_credentials",
                "name",
                "Synthetic add-on //user:secret@example.invalid/name",
            ),
            (
                "name_embedded_non_http_credentials",
                "name",
                "Synthetic add-on ssh://user:secret@example.invalid/name",
            ),
            (
                "repository_absolute_credentials",
                "repository",
                "https://user:secret@example.invalid/repository",
            ),
        )
        for name, field, credential_value in cases:
            with self.subTest(name=name):
                payload = live_equivalent_payload()
                payload["addon"][field] = credential_value
                transport = DetailTransport(call_result(payload))
                for addon in transport.addons:
                    if addon.get("slug") == UPSTREAM_ADDON_SLUG:
                        addon[field] = credential_value
                provider = configured_provider(transport)

                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as caught:
                    await provider.get_addon(UPSTREAM_ADDON_SLUG)

                self.assertEqual(
                    caught.exception.category,
                    "addon_response_contract_mismatch",
                )
                self.assertFalse(caught.exception.dispatched)
                health = provider.health_snapshot()
                self.assertNotIn(credential_value, json.dumps(health))
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)

    async def test_embedded_sensitive_identity_never_enters_plan_or_health(
        self,
    ):
        values = (
            (
                "embedded_token",
                "Synthetic add-on credential ghp_" + "A" * 36,
            ),
            (
                "embedded_protocol_relative_url",
                "Synthetic add-on //user:secret@example.invalid/name",
            ),
            (
                "embedded_non_http_url",
                "Synthetic add-on ssh://user:secret@example.invalid/name",
            ),
        )
        for kind, credential_value in values:
            with self.subTest(kind=kind):
                payload = live_equivalent_payload()
                payload["addon"]["name"] = credential_value
                transport = DetailTransport(call_result(payload))
                for addon in transport.addons:
                    if addon.get("slug") == UPSTREAM_ADDON_SLUG:
                        addon["name"] = credential_value
                provider = configured_provider(transport)
                with tempfile.TemporaryDirectory() as directory:
                    repository = ChangePlanRepository(
                        Path(directory) / "plans"
                    )
                    audit_path = Path(directory) / "audit.jsonl"
                    service = ChangeGovernanceService(
                        repository,
                        LegacyGateway(),
                        AuditLogger(
                            str(audit_path),
                            "synthetic-access-secret-value",
                        ),
                        lifecycle_gateway=planning_gateway(provider),
                    )

                    with self.assertRaises(GovernanceError):
                        await service.create_addon_restart_plan(
                            addon_slug=UPSTREAM_ADDON_SLUG,
                            expiration_minutes=5,
                        )

                    plans = repository.list()
                    retained = json.dumps(
                        {
                            "plans": [
                                plan.to_dict() for plan in plans
                            ],
                            "health": provider.health_snapshot(),
                        },
                        sort_keys=True,
                    )
                    if audit_path.exists():
                        retained += audit_path.read_text(encoding="utf-8")
                self.assertEqual(plans, [])
                self.assertNotIn(credential_value, retained)
                health = provider.health_snapshot()
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)
                self.assertNotIn(
                    "ha_manage_addon",
                    [tool_name for tool_name, _ in transport.calls],
                )

    async def test_release_change_between_inventory_and_detail_creates_no_plan(
        self,
    ):
        class AlternatingReleaseTransport(DetailTransport):
            async def execute_read(self, tool_name, arguments, **kwargs):
                if tool_name == "ha_get_addon" and "slug" in arguments:
                    self.catalog = FakeMcpTransport("7.14.2").catalog
                return await super().execute_read(
                    tool_name,
                    arguments,
                    **kwargs,
                )

        transport = AlternatingReleaseTransport()
        provider = configured_provider(transport)
        with tempfile.TemporaryDirectory() as directory:
            repository = ChangePlanRepository(Path(directory) / "plans")
            service = ChangeGovernanceService(
                repository,
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=planning_gateway(provider),
            )

            with self.assertRaises(GovernanceError) as caught:
                await service.create_addon_restart_plan(
                    addon_slug=UPSTREAM_ADDON_SLUG,
                    expiration_minutes=5,
                )

            self.assertEqual(repository.list(), [])
        self.assertEqual(
            caught.exception.code,
            ErrorCode.OPERATIONAL_CONTRACT_MISMATCH,
        )
        health = provider.health_snapshot()
        diagnostics = health["lifecycle_addon_response_diagnostics"]
        self.assertEqual(
            diagnostics["identity_mismatch_fields"],
            ["provider_contract"],
        )
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_raw_policy_drift_between_reads_remains_diagnostic(self):
        class RawPolicyDriftTransport(DetailTransport):
            before_raw_fingerprint: str | None = None
            after_raw_fingerprint: str | None = None

            async def execute_read(self, tool_name, arguments, **kwargs):
                if tool_name == "ha_get_addon" and "slug" in arguments:
                    self.before_raw_fingerprint = catalog_fingerprint(
                        self.catalog.tools
                    )
                    tools = deepcopy(self.catalog.tools)
                    for descriptor in tools:
                        policy = descriptor["_meta"]["ha_mcp"]["policy"]
                        policy.update(
                            {
                                "deployment": "addon",
                                "enabled": True,
                                "live": True,
                            }
                        )
                    self.catalog = replace(
                        self.catalog,
                        tools=tuple(tools),
                    )
                    self.after_raw_fingerprint = catalog_fingerprint(
                        self.catalog.tools
                    )
                return await super().execute_read(
                    tool_name,
                    arguments,
                    **kwargs,
                )

        transport = RawPolicyDriftTransport()
        provider = configured_provider(transport)

        addon = await provider.get_addon(UPSTREAM_ADDON_SLUG)

        self.assertEqual(addon["version"], "8.0.0")
        self.assertNotEqual(
            transport.before_raw_fingerprint,
            transport.after_raw_fingerprint,
        )
        health = provider.health_snapshot()
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_wrong_protocol_creates_no_plan(self):
        transport = DetailTransport()
        provider = configured_provider(transport)
        await provider.get_addon(UPSTREAM_ADDON_SLUG)
        transport.catalog = replace(
            transport.catalog,
            protocol_version="2024-11-05",
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = ChangePlanRepository(Path(directory) / "plans")
            service = ChangeGovernanceService(
                repository,
                LegacyGateway(),
                AuditLogger(
                    str(Path(directory) / "audit.jsonl"),
                    "synthetic-access-secret-value",
                ),
                lifecycle_gateway=planning_gateway(provider),
            )

            with self.assertRaises(GovernanceError) as caught:
                await service.create_addon_restart_plan(
                    addon_slug=UPSTREAM_ADDON_SLUG,
                    expiration_minutes=5,
                )

            self.assertEqual(repository.list(), [])
        self.assertEqual(
            caught.exception.code,
            ErrorCode.OPERATIONAL_CONTRACT_MISMATCH,
        )
        health = provider.health_snapshot()
        self.assertIsNone(
            health["lifecycle_addon_response_contract_model"]
        )
        self.assertIsNone(
            health["lifecycle_addon_response_envelope_variant"]
        )
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_identity_and_model_failures_create_no_plan(self):
        response_key = (
            "ha-mcp-v8.0.0-d65630f6",
            "8.0.0",
            "2025-03-26",
        )
        for kind in (
            "wrong_endpoint_host",
            "wrong_installed_version",
            "wrong_repository",
            "unsupported_response_model",
            "unknown_release",
        ):
            with self.subTest(kind=kind):
                transport = DetailTransport()
                endpoint = (
                    "http://abcdef12-ha-mcp:9583/"
                    "synthetic-upstream-secret/mcp"
                )
                payload = live_equivalent_payload()
                target = next(
                    addon
                    for addon in transport.addons
                    if addon["slug"] == UPSTREAM_ADDON_SLUG
                )
                contract_override = None
                if kind == "wrong_endpoint_host":
                    endpoint = (
                        "http://different-addon:9583/"
                        "synthetic-upstream-secret/mcp"
                    )
                elif kind == "wrong_installed_version":
                    target["version"] = "8.0.1"
                    payload["addon"]["version"] = "8.0.1"
                elif kind == "wrong_repository":
                    target["repository"] = "ffffffff"
                    payload["addon"]["repository"] = "ffffffff"
                elif kind == "unsupported_response_model":
                    contract_override = lifecycle.LifecycleAddonResponseContract(
                        model="unsupported-lifecycle-response-v1",
                        envelope_variant=(
                            LIFECYCLE_ADDON_RESPONSE_ENVELOPE_STRUCTURED
                        ),
                    )
                else:
                    transport.catalog = replace(
                        transport.catalog,
                        server_version="8.0.1",
                    )
                transport.detail_result = call_result(payload)
                provider = ReviewedOperationalLifecycleProvider()
                provider.configure(
                    lifecycle_settings(endpoint),
                    transport=transport,
                )
                with tempfile.TemporaryDirectory() as directory:
                    repository = ChangePlanRepository(
                        Path(directory) / "plans"
                    )
                    service = ChangeGovernanceService(
                        repository,
                        LegacyGateway(),
                        AuditLogger(
                            str(Path(directory) / "audit.jsonl"),
                            "synthetic-access-secret-value",
                        ),
                        lifecycle_gateway=planning_gateway(provider),
                    )

                    async def create_plan():
                        return await service.create_addon_restart_plan(
                            addon_slug=UPSTREAM_ADDON_SLUG,
                            expiration_minutes=5,
                        )

                    with self.assertRaises(GovernanceError):
                        if contract_override is None:
                            await create_plan()
                        else:
                            with patch.dict(
                                lifecycle._LIFECYCLE_ADDON_RESPONSE_CONTRACTS,
                                {response_key: contract_override},
                            ):
                                await create_plan()

                    plans = repository.list()
                self.assertEqual(plans, [])
                self.assertNotIn(
                    "ha_manage_addon",
                    [tool_name for tool_name, _ in transport.calls],
                )
                health = provider.health_snapshot()
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)

    async def test_unknown_release_clears_response_model_observability(self):
        transport = DetailTransport()
        provider = configured_provider(transport)
        await provider.get_addon(UPSTREAM_ADDON_SLUG)
        transport.catalog = replace(
            transport.catalog,
            server_version="8.0.1",
        )

        with self.assertRaises(OperationalLifecycleProviderError) as caught:
            await provider.probe("restart_addon")

        self.assertEqual(caught.exception.category, "upstream_version_mismatch")
        self.assertFalse(caught.exception.dispatched)
        health = provider.health_snapshot()
        self.assertIsNone(health["selected_compatibility_entry_id"])
        self.assertEqual(health["observed_upstream_version"], "8.0.1")
        self.assertIsNone(
            health["lifecycle_addon_response_contract_model"]
        )
        self.assertIsNone(
            health["lifecycle_addon_response_envelope_variant"]
        )
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_untrusted_version_is_not_retained_in_health(self):
        values = (
            "https://user:secret@example.invalid/" + "x" * 5_000,
            "8.0.1\nsecret",
        )
        for value in values:
            with self.subTest(kind="control" if "\n" in value else "long"):
                transport = DetailTransport()
                provider = configured_provider(transport)
                transport.catalog = replace(
                    transport.catalog,
                    server_version=value,
                )

                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as caught:
                    await provider.probe("restart_addon")

                self.assertEqual(
                    caught.exception.category,
                    "upstream_version_mismatch",
                )
                health = provider.health_snapshot()
                self.assertIsNone(health["observed_upstream_version"])
                self.assertNotIn(value, json.dumps(health))
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)

    async def test_identity_drift_is_bounded_and_fails_closed(self):
        cases = (
            ("slug_drift", "slug", "different_addon"),
            ("name_drift", "name", "Different name"),
            ("version_drift", "version", "8.0.1"),
            ("state_drift", "state", "stopped"),
            ("state_wrong_type", "state", None),
            ("repository_drift", "repository", "ffffffff"),
            ("repository_unknown_drift", "repository", None),
            (
                "update_available_wrong_type",
                "update_available",
                "false",
            ),
            ("update_available_drift", "update_available", True),
            ("update_available_unknown_drift", "update_available", None),
        )
        for name, field, value in cases:
            with self.subTest(name=name):
                payload = live_equivalent_payload()
                payload["addon"][field] = value
                provider = configured_provider(
                    DetailTransport(call_result(payload))
                )

                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as caught:
                    await provider.get_addon(UPSTREAM_ADDON_SLUG)

                self.assertEqual(
                    caught.exception.category,
                    "addon_response_contract_mismatch",
                )
                self.assertFalse(caught.exception.dispatched)
                health = provider.health_snapshot()
                diagnostics = health[
                    "lifecycle_addon_response_diagnostics"
                ]
                self.assertIsInstance(diagnostics, dict)
                if isinstance(value, str) and value != "false":
                    self.assertNotIn(value, json.dumps(diagnostics))
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)

    async def test_stopped_state_is_a_reviewed_exact_release_value(self):
        transport = DetailTransport()
        for addon in transport.addons:
            if addon["slug"] == UPSTREAM_ADDON_SLUG:
                addon["state"] = "stopped"
        payload = live_equivalent_payload()
        payload["addon"]["state"] = "stopped"
        transport.detail_result = call_result(payload)
        provider = configured_provider(transport)

        addon = await provider.get_addon(UPSTREAM_ADDON_SLUG)

        self.assertEqual(addon["state"], "stopped")
        health = provider.health_snapshot()
        self.assertEqual(sum(health["dispatch_counts"].values()), 0)
        self.assertEqual(health["fallback_count"], 0)

    async def test_matching_invalid_identity_values_fail_closed(self):
        for name, field, value in (
            ("state", "state", "unknown"),
            ("repository", "repository", "ffffffff"),
        ):
            with self.subTest(name=name):
                transport = DetailTransport()
                for addon in transport.addons:
                    if addon["slug"] == UPSTREAM_ADDON_SLUG:
                        addon[field] = value
                payload = live_equivalent_payload()
                payload["addon"][field] = value
                transport.detail_result = call_result(payload)
                provider = configured_provider(transport)

                with self.assertRaises(
                    OperationalLifecycleProviderError
                ) as caught:
                    await provider.get_addon(UPSTREAM_ADDON_SLUG)

                self.assertEqual(
                    caught.exception.category,
                    "addon_response_contract_mismatch",
                )
                self.assertFalse(caught.exception.dispatched)
                health = provider.health_snapshot()
                self.assertEqual(sum(health["dispatch_counts"].values()), 0)
                self.assertEqual(health["fallback_count"], 0)


if __name__ == "__main__":
    unittest.main()
