"""HAMCP-209: build the dependency index from loaded automation configuration.

Owner decisions D3/D4 (HAMCP-171): read Home Assistant's loaded configuration
with a bounded number of requests instead of one serialized REST read per
automation, and never read a configuration file for it. These tests prove:

* parity: the loaded source yields an index identical to the stored REST path
  for plain, templated, dynamic, label, blueprint, unavailable and id-less
  automations;
* coverage and partial reporting are unchanged;
* every stored fallback is reported with its reason, never silently;
* the request count is bounded (one WebSocket connection, no per-automation
  REST read);
* the pending-write journal keeps a write that Core has not reloaded yet from
  being reported from stale loaded configuration;
* the batch client refuses any command outside its fixed read-only set;
* no analysis output carries a secret-shaped value resolved into loaded
  configuration (a package automation's ``!secret``).

Every value is synthetic. The WebSocket client tests use a disposable loopback
peer; no live dependency is used.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.clients.websocket import (  # noqa: E402
    BATCH_WINDOW,
    MAX_BATCH_COMMANDS,
    HomeAssistantWebSocketClient,
    batch_command_allowed,
)
from ha_mcp_engineering.configuration import Settings  # noqa: E402
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    MAX_PENDING_AUTOMATION_WRITES,
    DirectHaDependencyProvider,
    _display_safe_effect_tokens,
)
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.governance.entity_metadata_disclosure import (  # noqa: E402
    build_consumer_disclosure,
)
from ha_mcp_engineering.governance.helper_dependency import HelperDependencyRiskService  # noqa: E402
from ha_mcp_engineering.handoff.provider import EngineeringHandoffProvider  # noqa: E402
from ha_mcp_engineering.handoff.service import HandoffGenerationService  # noqa: E402
from ha_mcp_engineering.impact.provider import DirectHaImpactProvider  # noqa: E402
from ha_mcp_engineering.impact.service import ChangeImpactAnalysisService  # noqa: E402
from ha_mcp_engineering.incident.provider import DirectHaIncidentProvider  # noqa: E402
from ha_mcp_engineering.incident.service import IncidentCorrelationService  # noqa: E402
from ha_mcp_engineering.integrity.provider import DirectHaIntegrityProvider  # noqa: E402
from ha_mcp_engineering.integrity.service import ConfigurationIntegrityAnalysisService  # noqa: E402
from ha_mcp_engineering.reliability.provider import DirectHaReliabilityProvider  # noqa: E402
from ha_mcp_engineering.reliability.service import AutomationReliabilityAnalysisService  # noqa: E402
from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime  # noqa: E402
from ha_mcp_engineering.errors import (  # noqa: E402
    AuthorizationError,
    HomeAssistantApiError,
    HomeAssistantUnavailableError,
)

from tests.hamcp209_fixtures import (  # noqa: E402
    SyntheticHomeAssistant,
    SyntheticRest,
    SyntheticWebSocket,
    generated_automations,
    read_blueprint,
    record,
    representative_automations,
)


SECRET = "synthetic-not-a-credential"
BLUEPRINT_READER = "ha_mcp_engineering.dependency.provider._read_blueprint_with_status"


def provider_for(ha: SyntheticHomeAssistant, *, batch: bool = True) -> DirectHaDependencyProvider:
    websocket = ha.websocket if batch else ha.websocket_without_batch
    return DirectHaDependencyProvider(ha.rest, websocket, secret=SECRET)


def coverage_of(result, source_type="automation"):
    return next(item for item in result.coverage if item.source_type == source_type)


def _rows(items) -> list[str]:
    return sorted(json.dumps(asdict(item), sort_keys=True, default=str) for item in items)


def index_view(result) -> dict:
    """Everything the index consumes from a scan, except timing and source."""

    return {
        "findings": _rows(result.findings),
        "dynamic_references": _rows(result.dynamic_references),
        "obligations": _rows(result.obligations),
        "automation_action_profiles": _rows(result.automation_action_profiles),
        "automation_read_failures": _rows(result.automation_read_failures),
        "target_metadata": result.target_metadata,
        "label_memberships": result.label_memberships,
        "label_membership_fingerprints": result.label_membership_fingerprints,
        "label_membership_complete": result.label_membership_complete,
        "label_registry_complete": result.label_registry_complete,
        "label_selector_authority": {
            key: asdict(value) for key, value in result.label_selector_authority.items()
        },
        "coverage": [
            {
                key: value
                for key, value in item.public().items()
                if key not in {"duration_ms", "policy", "warnings"}
            }
            for item in result.coverage
        ],
        "home_assistant_version": result.home_assistant_version,
    }


class _FixedProvider:
    """Serve a recorded scan so two indexes can be compared."""

    provider_id = "direct_ha_api"

    def __init__(self, result) -> None:
        self.result = result

    async def scan(self):
        return self.result


class ExtractionParityTests(unittest.IsolatedAsyncioTestCase):
    async def scans(self, automations):
        ha_loaded = SyntheticHomeAssistant(deepcopy(automations))
        ha_stored = SyntheticHomeAssistant(deepcopy(automations))
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            loaded = await provider_for(ha_loaded).scan()
            stored = await provider_for(ha_stored, batch=False).scan()
        return ha_loaded, loaded, ha_stored, stored

    async def test_representative_fixture_index_is_identical(self):
        ha, loaded, _, stored = await self.scans(representative_automations())

        self.assertEqual(index_view(loaded), index_view(stored))
        self.assertEqual(loaded.profile["automation_config_source"], "mixed")
        self.assertEqual(stored.profile["automation_config_source"], "stored_rest")
        # The fixture exercises each relation the extraction supports here.
        relations = {item.relation for item in loaded.findings}
        self.assertIn("blueprint_input", relations)
        self.assertTrue(loaded.dynamic_references)
        self.assertIn("synthetic_downstairs", loaded.label_memberships)
        reasons = {item.reason_code for item in loaded.automation_read_failures}
        self.assertEqual(reasons, {"automation_id_missing"})
        # The blueprint and the unavailable entity read stored; the rest are loaded.
        self.assertEqual(
            loaded.profile["stored_config_fallback_reasons"],
            {"blueprint_instance": 1, "loaded_entity_unavailable": 1},
        )
        self.assertEqual(
            loaded.profile["automation_config_source_counts"],
            {"loaded_websocket": 4, "stored_rest": 2},
        )
        self.assertEqual(ha.rest_counts["automation_config_read"], 2)

    async def test_snapshot_fingerprint_is_identical(self):
        _, loaded, _, stored = await self.scans(representative_automations())

        loaded_index, _, _ = await DependencyIndex(_FixedProvider(loaded)).get(refresh=True)
        stored_index, _, _ = await DependencyIndex(_FixedProvider(stored)).get(refresh=True)
        self.assertEqual(loaded_index.fingerprint, stored_index.fingerprint)

    async def test_bulk_fixture_index_is_identical(self):
        _, loaded, _, stored = await self.scans(generated_automations(113))

        self.assertEqual(index_view(loaded), index_view(stored))
        self.assertEqual(coverage_of(loaded).completeness, "complete")

    async def test_blueprint_loaded_form_is_never_used(self):
        """Core's loaded form of a blueprint is substituted; stored keeps inputs."""

        automations = representative_automations()
        ha = SyntheticHomeAssistant(automations)
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            result = await provider_for(ha).scan()
        blueprint_findings = [
            item for item in result.findings if item.source_id == "synthetic_blueprint"
        ]
        self.assertTrue(any(item.relation == "blueprint_input" for item in blueprint_findings))

        # search/related alone identifies it, even when loaded hides use_blueprint.
        ha = SyntheticHomeAssistant(representative_automations())
        ha.automations["automation.synthetic_blueprint"]["loaded"].pop("use_blueprint", None)
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            result = await provider_for(ha).scan()
        self.assertEqual(result.profile["stored_config_fallback_reasons"]["blueprint_instance"], 1)


class BoundedRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_113_automations_use_one_connection_and_no_per_automation_read(self):
        ha = SyntheticHomeAssistant(generated_automations(113))
        result = await provider_for(ha).scan()

        self.assertEqual(ha.rest_counts["automation_config_read"], 0)
        self.assertEqual(len(ha.batch_calls), 1)
        self.assertEqual(len(ha.batch_calls[0]), 226)
        operations = result.profile["request_count_by_operation"]
        self.assertNotIn("automation_config", operations)
        self.assertEqual(operations["loaded_automation_config_batch"], 1)
        # /config, /states, entity registry, label registry, one batch.
        self.assertLessEqual(result.profile["request_count"], 5)
        self.assertEqual(result.profile["loaded_config_connection_count"], 1)
        self.assertEqual(result.profile["loaded_config_command_count"], 226)
        self.assertEqual(result.profile["automation_config_source"], "loaded_websocket")
        self.assertEqual(coverage_of(result).completeness, "complete")

    async def test_batch_carries_only_available_id_bearing_candidates(self):
        ha = SyntheticHomeAssistant(representative_automations())
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            await provider_for(ha).scan()
        entities = {item.get("entity_id") or item.get("item_id") for item in ha.batch_calls[0]}
        self.assertNotIn("automation.synthetic_without_id", entities)
        self.assertNotIn("automation.synthetic_blueprint_missing", entities)
        self.assertEqual(len(entities), 5)

    async def test_no_automations_dispatch_no_batch(self):
        ha = SyntheticHomeAssistant([])
        result = await provider_for(ha).scan()
        self.assertEqual(ha.batch_calls, [])
        self.assertEqual(result.profile["loaded_config_connection_count"], 0)
        self.assertEqual(result.profile["automation_config_source"], "none")
        self.assertEqual(coverage_of(result).completeness, "complete")


class FallbackReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_per_automation_reason_is_reported(self):
        automations = representative_automations() + generated_automations(3)
        ha = SyntheticHomeAssistant(automations)
        ha.batch_overrides[("automation/config", "automation.synthetic_bulk_0000")] = (False, "not_found")
        ha.automations["automation.synthetic_bulk_0001"]["loaded"]["id"] = "synthetic_other"
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            result = await provider_for(ha).scan()

        reasons = result.profile["stored_config_fallback_reasons"]
        self.assertEqual(reasons, {
            "blueprint_instance": 1,
            "loaded_config_unreadable": 1,
            "loaded_entity_unavailable": 1,
            "loaded_identity_mismatch": 1,
        })
        policy = coverage_of(result).policy
        self.assertIn("automation_config_source=mixed", policy)
        for reason, count in reasons.items():
            self.assertIn(f"{reason}={count}", policy)
        # The stored reads succeeded, so coverage is unchanged.
        self.assertEqual(coverage_of(result).failed_item_count, 1)  # the id-less one

    async def test_malformed_loaded_results_are_reported(self):
        for override in (
            (True, {"config": "not-an-object"}),
            (True, {}),
            (True, None),
        ):
            with self.subTest(override=override):
                ha = SyntheticHomeAssistant(generated_automations(2))
                ha.batch_overrides[("automation/config", "automation.synthetic_bulk_0000")] = override
                result = await provider_for(ha).scan()
                self.assertEqual(
                    result.profile["stored_config_fallback_reasons"],
                    {"loaded_config_unreadable": 1},
                )
        for override in ((False, "unknown_error"), (True, {"automation_blueprint": "x"}), (True, [])):
            with self.subTest(search=override):
                ha = SyntheticHomeAssistant(generated_automations(2))
                ha.batch_overrides[("search/related", "automation.synthetic_bulk_0001")] = override
                result = await provider_for(ha).scan()
                self.assertEqual(
                    result.profile["stored_config_fallback_reasons"],
                    {"loaded_config_unreadable": 1},
                )

    async def test_whole_source_failure_is_reported_not_silent(self):
        for failure in (
            HomeAssistantUnavailableError(),
            AuthorizationError(),
            HomeAssistantApiError(details={"reason": "malformed_response"}),
        ):
            with self.subTest(failure=type(failure).__name__):
                ha = SyntheticHomeAssistant(generated_automations(4))
                ha.batch_failure = failure
                result = await provider_for(ha).scan()
                automation = coverage_of(result)
                self.assertEqual(result.profile["automation_config_source"], "stored_rest")
                self.assertEqual(
                    result.profile["stored_config_fallback_reasons"],
                    {"loaded_source_unavailable": 4},
                )
                self.assertIn("loaded_source_unavailable=4", automation.policy)
                self.assertTrue(any("Loaded automation configuration was unavailable" in item
                                    for item in automation.warnings))
                self.assertFalse(automation.fallback_occurred)
                self.assertEqual(ha.rest_counts["automation_config_read"], 4)
                self.assertEqual(automation.completeness, "complete")

    async def test_incomplete_batch_result_is_a_whole_source_failure(self):
        ha = SyntheticHomeAssistant(generated_automations(3))
        original = ha.websocket.command_batch

        async def short(payloads):
            return (await original(payloads))[:-1]

        ha.websocket.command_batch = short
        result = await provider_for(ha).scan()
        self.assertEqual(result.profile["stored_config_fallback_reasons"], {"loaded_source_unavailable": 3})


class CoverageTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreadable_loaded_and_stored_automation_is_partial(self):
        """A package automation missing from both routes stays a failure."""

        ha = SyntheticHomeAssistant(generated_automations(3))
        ha.batch_overrides[("automation/config", "automation.synthetic_bulk_0002")] = (False, "not_found")
        ha.automations["automation.synthetic_bulk_0002"]["stored"] = None
        result = await provider_for(ha).scan()

        automation = coverage_of(result)
        self.assertEqual(automation.completeness, "partial")
        self.assertEqual(automation.failed_item_count, 1)
        self.assertEqual(
            [(item.source_entity_id, item.reason_code) for item in result.automation_read_failures],
            [("automation.synthetic_bulk_0002", "automation_config_unreadable")],
        )
        self.assertEqual(result.profile["stored_config_fallback_reasons"], {"loaded_config_unreadable": 1})

    async def test_partial_coverage_matches_stored_path(self):
        automations = generated_automations(3)
        automations[1]["stored"] = None
        automations[1]["loaded"] = None
        ha_loaded = SyntheticHomeAssistant(deepcopy(automations))
        ha_stored = SyntheticHomeAssistant(deepcopy(automations))
        loaded = await provider_for(ha_loaded).scan()
        stored = await provider_for(ha_stored, batch=False).scan()
        self.assertEqual(index_view(loaded), index_view(stored))
        self.assertEqual(coverage_of(loaded).completeness, "partial")

    async def test_package_automation_is_readable_from_loaded_only(self):
        """The one intended difference: REST 404 for a package automation."""

        ha = SyntheticHomeAssistant(generated_automations(2))
        ha.automations["automation.synthetic_bulk_0001"]["stored"] = None
        result = await provider_for(ha).scan()
        self.assertEqual(coverage_of(result).completeness, "complete")
        self.assertEqual(result.profile["automation_config_source"], "loaded_websocket")


class PendingWriteJournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreloaded_write_reads_stored_until_reload(self):
        ha = SyntheticHomeAssistant(generated_automations(3))
        provider = provider_for(ha)
        entity = "automation.synthetic_bulk_0001"
        new_config = deepcopy(ha.automations[entity]["stored"])
        new_config["action"][0]["target"]["entity_id"] = "light.synthetic_written"
        provider.note_automation_write("synthetic_bulk_0001")
        await ha.rest.request("POST", "/config/automation/config/synthetic_bulk_0001", body=new_config)

        result = await provider.scan()
        targets = {item.target_entity_id for item in result.findings if item.source_id == "synthetic_bulk_0001"}
        self.assertIn("light.synthetic_written", targets)
        self.assertEqual(result.profile["stored_config_fallback_reasons"], {"pending_write_reload": 1})
        self.assertEqual(result.profile["pending_write_journal"]["pending_at_build"], 1)
        self.assertIn("synthetic_bulk_0001", provider._pending_writes)

        ha.reload(entity)
        result = await provider.scan()
        targets = {item.target_entity_id for item in result.findings if item.source_id == "synthetic_bulk_0001"}
        self.assertIn("light.synthetic_written", targets)
        self.assertEqual(result.profile["stored_config_fallback_reasons"], {})
        self.assertEqual(result.profile["pending_write_journal"]["verified_cleared"], 1)
        self.assertEqual(provider._pending_writes, {})

        ha.rest_counts.clear()
        await provider.scan()
        self.assertEqual(ha.rest_counts["automation_config_read"], 0)

    async def test_written_automation_not_loaded_is_a_coverage_failure(self):
        ha = SyntheticHomeAssistant(generated_automations(2))
        provider = provider_for(ha)
        provider.note_automation_write("synthetic_new")
        await ha.rest.request(
            "POST", "/config/automation/config/synthetic_new",
            body={"id": "synthetic_new", "trigger": [], "action": []},
        )
        result = await provider.scan()
        automation = coverage_of(result)
        self.assertEqual(automation.completeness, "partial")
        self.assertIn(
            ("synthetic_new", "automation_pending_reload_not_loaded"),
            [(item.source_id, item.reason_code) for item in result.automation_read_failures],
        )
        self.assertTrue(any("stored but not loaded" in item for item in automation.warnings))
        self.assertEqual(result.profile["pending_write_journal"]["not_loaded"], 1)
        self.assertIn("synthetic_new", provider._pending_writes)

    async def test_written_automation_not_loaded_is_partial_with_empty_inventory(self):
        ha = SyntheticHomeAssistant([])
        provider = provider_for(ha)
        provider.note_automation_write("synthetic_first")
        await ha.rest.request(
            "POST", "/config/automation/config/synthetic_first",
            body={"id": "synthetic_first", "trigger": [], "action": []},
        )
        result = await provider.scan()
        automation = coverage_of(result)
        self.assertEqual(automation.completeness, "partial")
        self.assertEqual(automation.failed_item_count, 1)

    async def test_deleted_pending_automation_clears_on_404(self):
        ha = SyntheticHomeAssistant(generated_automations(2))
        provider = provider_for(ha)
        provider.note_automation_write("synthetic_gone")
        result = await provider.scan()
        self.assertEqual(coverage_of(result).completeness, "complete")
        self.assertEqual(provider._pending_writes, {})

    async def test_unreadable_pending_absent_automation_is_not_cleared(self):
        ha = SyntheticHomeAssistant(generated_automations(1))
        provider = provider_for(ha)
        provider.note_automation_write("synthetic_unknown")
        original = ha.rest.request

        async def failing(method, path, *args, **kwargs):
            if str(path).endswith("/synthetic_unknown"):
                raise HomeAssistantUnavailableError()
            return await original(method, path, *args, **kwargs)

        ha.rest.request = failing
        result = await provider.scan()
        self.assertEqual(coverage_of(result).completeness, "partial")
        self.assertIn("synthetic_unknown", provider._pending_writes)

    async def test_stored_read_failure_for_pending_write_is_not_cleared(self):
        ha = SyntheticHomeAssistant(generated_automations(2))
        provider = provider_for(ha)
        provider.note_automation_write("synthetic_bulk_0000")
        ha.automations["automation.synthetic_bulk_0000"]["stored"] = None
        result = await provider.scan()
        self.assertEqual(coverage_of(result).completeness, "partial")
        self.assertIn("synthetic_bulk_0000", provider._pending_writes)

    async def test_rewrite_during_build_is_retained(self):
        ha = SyntheticHomeAssistant(generated_automations(2))
        provider = provider_for(ha)
        provider.note_automation_write("synthetic_bulk_0000")
        original = ha.websocket.command_batch

        async def rewrite_during(payloads):
            provider.note_automation_write("synthetic_bulk_0000")
            return await original(payloads)

        ha.websocket.command_batch = rewrite_during
        await provider.scan()
        self.assertIn("synthetic_bulk_0000", provider._pending_writes)

    async def test_stable_reason_clears_after_stored_read(self):
        ha = SyntheticHomeAssistant(representative_automations())
        provider = provider_for(ha)
        provider.note_automation_write("synthetic_blueprint")
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            await provider.scan()
        self.assertEqual(provider._pending_writes, {})

    async def test_journal_overflow_compares_every_candidate_until_clean(self):
        ha = SyntheticHomeAssistant(generated_automations(3))
        provider = provider_for(ha)
        for index in range(MAX_PENDING_AUTOMATION_WRITES + 1):
            provider.note_automation_write(f"synthetic_written_{index}")
        self.assertTrue(provider._pending_write_overflow)
        self.assertEqual(len(provider._pending_writes), MAX_PENDING_AUTOMATION_WRITES)

        result = await provider.scan()
        automation = coverage_of(result)
        self.assertEqual(automation.completeness, "partial")
        self.assertTrue(result.profile["pending_write_journal"]["overflow_at_build"])
        self.assertEqual(ha.rest_counts["automation_config_read"], 3 + MAX_PENDING_AUTOMATION_WRITES)
        self.assertTrue(any("journal overflowed" in item for item in automation.warnings))
        self.assertFalse(provider._pending_write_overflow)
        self.assertEqual(provider._pending_writes, {})

        ha.rest_counts.clear()
        result = await provider.scan()
        self.assertEqual(coverage_of(result).completeness, "complete")
        self.assertEqual(ha.rest_counts["automation_config_read"], 0)

    async def test_journal_overflow_with_stale_loaded_is_retained(self):
        ha = SyntheticHomeAssistant(generated_automations(2))
        provider = provider_for(ha)
        for index in range(MAX_PENDING_AUTOMATION_WRITES + 1):
            provider.note_automation_write(f"synthetic_written_{index}")
        ha.automations["automation.synthetic_bulk_0001"]["stored"]["alias"] = "Synthetic changed"
        result = await provider.scan()
        self.assertEqual(result.profile["stored_config_fallback_reasons"], {"pending_write_reload": 1})
        self.assertTrue(provider._pending_write_overflow)

    async def test_runtime_records_writes_on_the_configured_provider(self):
        runtime = DependencyAnalysisRuntime()
        runtime.note_automation_write("synthetic_unconfigured")  # no-op
        ha = SyntheticHomeAssistant([])
        runtime.configure(ha.rest, ha.websocket, secret=SECRET)
        runtime.note_automation_write("synthetic_bulk_0000")
        self.assertIn("synthetic_bulk_0000", runtime.require().index.provider._pending_writes)
        await runtime.shutdown()


class GovernedWriteHookTests(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(self, journal):
            self.journal = journal
            self.calls = []

        async def request(self, method, path, body=None, **kwargs):
            # The journal entry must exist before the write is dispatched.
            self.calls.append((method, str(path), list(self.journal)))
            return {"result": "ok"}

    async def test_automation_gateway_journals_before_dispatch(self):
        from ha_mcp_engineering.governance.service import AutomationGateway

        journal = []
        client = self.Client(journal)
        with patch(
            "ha_mcp_engineering.dependency.DEPENDENCY_ANALYSIS.note_automation_write",
            side_effect=journal.append,
        ):
            await AutomationGateway(client).write(
                "synthetic_hook", {"id": "synthetic_hook", "trigger": [], "action": []}
            )
        self.assertEqual(client.calls, [
            ("POST", "/config/automation/config/synthetic_hook", ["synthetic_hook"]),
        ])

    async def test_invalid_identifier_is_not_journaled(self):
        from ha_mcp_engineering.governance.resources import ConfigurationMutationNotDispatchedError
        from ha_mcp_engineering.governance.service import AutomationGateway

        journal = []
        with patch(
            "ha_mcp_engineering.dependency.DEPENDENCY_ANALYSIS.note_automation_write",
            side_effect=journal.append,
        ):
            with self.assertRaises(ConfigurationMutationNotDispatchedError):
                await AutomationGateway(self.Client(journal)).write("../unsafe", {})
        self.assertEqual(journal, [])

    async def test_resource_gateway_journals_automation_only(self):
        from ha_mcp_engineering.governance.resources import ConfigurationResourceGateway

        journal = []
        client = self.Client(journal)
        gateway = ConfigurationResourceGateway(client, object())
        with patch(
            "ha_mcp_engineering.dependency.DEPENDENCY_ANALYSIS.note_automation_write",
            side_effect=journal.append,
        ):
            await gateway._write_rest_configuration(
                "automation", "synthetic_hook", {"id": "synthetic_hook", "trigger": [], "action": []}
            )
            await gateway._write_rest_configuration(
                "script", "synthetic_script", {"sequence": []}
            )
        self.assertEqual(journal, ["synthetic_hook"])
        self.assertEqual(client.calls[0][2], ["synthetic_hook"])


class BatchAllowlistTests(unittest.TestCase):
    def test_only_fixed_read_shapes_are_admitted(self):
        self.assertTrue(batch_command_allowed({"type": "automation/config", "entity_id": "automation.synthetic"}))
        self.assertTrue(batch_command_allowed(
            {"type": "search/related", "item_type": "automation", "item_id": "automation.synthetic"}
        ))
        refused = [
            {"type": "automation/config", "entity_id": "script.synthetic"},
            {"type": "automation/config", "entity_id": "automation.Synthetic"},
            {"type": "automation/config", "entity_id": "automation.synthetic", "extra": 1},
            {"type": "automation/config"},
            {"type": "search/related", "item_type": "script", "item_id": "automation.synthetic"},
            {"type": "search/related", "item_type": "automation", "item_id": "light.synthetic"},
            {"type": "script/config", "entity_id": "automation.synthetic"},
            {"type": "call_service", "domain": "automation", "service": "reload"},
            {"type": "config/automation/delete", "entity_id": "automation.synthetic"},
            {"type": "subscribe_events", "event_type": "automation_reloaded"},
            {"type": "automation/config", "entity_id": "automation.synthetic", "id": 7},
            ["automation/config"],
            None,
        ]
        for payload in refused:
            with self.subTest(payload=payload):
                self.assertFalse(batch_command_allowed(payload))


class _Peer:
    """Disposable loopback WebSocket peer with Core's result-message shape."""

    def __init__(self, handler):
        self.handler = handler
        self.runner = None
        self.url = ""
        self.connections = 0
        self.max_in_flight = 0

    async def start(self):
        app = web.Application()
        app.router.add_get("/api/websocket", self._handle)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"

    async def stop(self):
        if self.runner is not None:
            await self.runner.cleanup()

    async def _handle(self, request):
        self.connections += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await self.handler(self, ws)
        await ws.close()
        return ws


async def _authenticate(ws):
    await ws.send_json({"type": "auth_required"})
    await ws.receive_json()
    await ws.send_json({"type": "auth_ok"})


def _settings(url):
    return Settings(
        ha_url=url,
        ha_token="synthetic-no-real-token",
        access_secret="synthetic-no-real-secret",
        port=8100,
        audit_path="unused-synthetic",
        rate_limit_per_minute=120,
        rate_limit_burst=20,
        destructive_services=frozenset(),
        ha_timeout_seconds=5.0,
    )


def _payloads(count):
    return [{"type": "automation/config", "entity_id": f"automation.synthetic_{index}"} for index in range(count)]


class CommandBatchClientTests(unittest.IsolatedAsyncioTestCase):
    async def run_peer(self, handler, payloads):
        peer = _Peer(handler)
        await peer.start()
        try:
            client = HomeAssistantWebSocketClient(_settings(peer.url))
            return peer, await client.command_batch(payloads)
        finally:
            await peer.stop()

    async def test_pipelined_results_in_order_with_bounded_window(self):
        async def handler(peer, ws):
            await _authenticate(ws)
            pending = []
            answered = 0
            while answered < 40:
                while len(pending) < BATCH_WINDOW + 1:
                    try:
                        command = await asyncio.wait_for(ws.receive_json(), 0.05)
                    except asyncio.TimeoutError:
                        break
                    pending.append(command)
                peer.max_in_flight = max(peer.max_in_flight, len(pending))
                # Answer newest first, with an event in between.
                command = pending.pop()
                await ws.send_json({"type": "event", "event": {}})
                if command["entity_id"].endswith("_3"):
                    await ws.send_json({"id": command["id"], "type": "result", "success": False,
                                        "error": {"code": "not_found", "message": "synthetic"}})
                else:
                    await ws.send_json({"id": command["id"], "type": "result", "success": True,
                                        "result": {"config": {"id": command["entity_id"]}}})
                answered += 1

        peer, results = await self.run_peer(handler, _payloads(40))
        self.assertEqual(peer.connections, 1)
        self.assertLessEqual(peer.max_in_flight, BATCH_WINDOW)
        self.assertEqual(len(results), 40)
        self.assertEqual(results[3], (False, "not_found"))
        self.assertEqual(results[5], (True, {"config": {"id": "automation.synthetic_5"}}))

    async def test_refused_payload_never_connects(self):
        peer = _Peer(_authenticate)
        await peer.start()
        try:
            client = HomeAssistantWebSocketClient(_settings(peer.url))
            for payloads in (
                [*_payloads(2), {"type": "call_service", "domain": "light", "service": "turn_on"}],
                _payloads(MAX_BATCH_COMMANDS + 1),
            ):
                with self.assertRaises(HomeAssistantApiError) as caught:
                    await client.command_batch(payloads)
                self.assertEqual(caught.exception.details["reason"], "command_not_allowed")
            self.assertEqual(await client.command_batch([]), [])
        finally:
            await peer.stop()
        self.assertEqual(peer.connections, 0)

    async def test_authorization_error_result_fails_the_batch(self):
        async def handler(peer, ws):
            await _authenticate(ws)
            command = await ws.receive_json()
            await ws.send_json({"id": command["id"], "type": "result", "success": False,
                                "error": {"code": "unauthorized", "message": "synthetic"}})

        with self.assertRaises(AuthorizationError):
            await self.run_peer(handler, _payloads(1))

    async def test_rejected_authentication_fails_the_batch(self):
        async def handler(peer, ws):
            await ws.send_json({"type": "auth_required"})
            await ws.receive_json()
            await ws.send_json({"type": "auth_invalid"})

        with self.assertRaises(AuthorizationError):
            await self.run_peer(handler, _payloads(1))

    async def test_protocol_violations_fail_the_batch(self):
        async def duplicate(peer, ws):
            await _authenticate(ws)
            command = await ws.receive_json()
            for _ in range(2):
                await ws.send_json({"id": command["id"], "type": "result", "success": True, "result": {}})
            await ws.receive_json()

        async def unknown(peer, ws):
            await _authenticate(ws)
            await ws.receive_json()
            await ws.send_json({"id": 99, "type": "result", "success": True, "result": {}})

        async def closed(peer, ws):
            await _authenticate(ws)
            await ws.receive_json()

        for handler in (duplicate, unknown, closed):
            with self.subTest(handler=handler.__name__):
                with self.assertRaises(HomeAssistantApiError) as caught:
                    await self.run_peer(handler, _payloads(2))
                self.assertEqual(caught.exception.details["reason"], "malformed_response")

    async def test_withdrawn_dispatch_authority_stops_sending(self):
        received = []

        async def handler(peer, ws):
            await _authenticate(ws)
            try:
                while True:
                    received.append(await asyncio.wait_for(ws.receive_json(), 1.0))
            except (asyncio.TimeoutError, TypeError):
                return

        class Telemetry:
            error_code = None
            calls = 0
            ha_duration_ms = 0.0
            timeout_occurred = False

            def __init__(self):
                self.endpoint_categories = set()

            def finish_ha_attempt(self, finished):
                pass

            def authorize_core_dispatch(self):
                self.calls += 1
                return self.calls <= 3

            def begin_ha_attempt(self, started):
                pass

        telemetry = Telemetry()
        with patch("ha_mcp_engineering.clients.websocket.current_telemetry", return_value=telemetry):
            with self.assertRaises(HomeAssistantUnavailableError):
                await self.run_peer(handler, _payloads(10))
        # One check before connecting, then one per send: two sends got out.
        self.assertEqual(len(received), 2)


# Resolved ``!secret`` values. Each planted value is assembled at run time
# around a distinctive marker, so no secret-shaped text is in this file.
_MARKERS = {
    name: f"hamcp209{name}plant"
    for name in (
        "jwt", "bearer", "password", "apikey", "userinfo", "webhook",
        "query", "template", "label", "target", "targetjwt", "safefield",
    )
}


def _jwt_shape(marker: str) -> str:
    return ".".join(["ey" + "J" + marker, "c3ludGhldGljcGF5bG9hZA", "c3ludGhldGljc2ln"])


def _bearer(marker: str) -> str:
    return "Bear" + "er " + marker


def _secret_bearing_config(automation_id: str) -> dict:
    m = _MARKERS
    credential_key = "pass" + "word"
    userinfo = "https://" + "synthetic" + ":" + m["userinfo"] + "@example.invalid/hook"
    query = "https://example.invalid/hook?" + "access_" + "token=" + m["query"]
    return {
        "id": automation_id,
        "alias": automation_id,
        "trigger": [
            {"platform": "state", "entity_id": "binary_sensor.synthetic_door"},
            {"platform": "webhook", "webhook_id": m["webhook"]},
        ],
        "condition": [
            {
                "condition": "template",
                "value_template": (
                    "{{ states('input_number.synthetic_setpoint') | float > 1 and '"
                    + credential_key + "=" + m["template"] + "' != '' }}"
                ),
            },
            {"condition": "state", "entity_id": "input_select.synthetic_mode", "state": "away"},
            {
                "condition": "template",
                "value_template": (
                    "{{ is_state('light.' ~ states('input_text.synthetic_target'), 'on') and '"
                    + _bearer(m["bearer"]) + "' != '" + _jwt_shape(m["jwt"]) + "' }}"
                ),
            },
        ],
        "action": [
            {
                "service": "light.turn_on",
                "target": {"entity_id": "light.synthetic_kitchen"},
                "data": {credential_key: m["password"], "api_key": m["apikey"], "brightness": 10},
            },
            {
                "service": "input_number.set_value",
                "target": {"entity_id": "input_number.synthetic_setpoint"},
                "data": {"value": 3},
            },
            {
                # Literal targets and allowlisted data fields are shown verbatim
                # by the effect projection unless the sanitizer recognizes them.
                "service": "light.turn_off",
                "target": {
                    "entity_id": "light.synthetic_hall",
                    "device_id": _bearer(m["target"]),
                    "label_id": _jwt_shape(m["targetjwt"]),
                },
                "data": {"transition": _bearer(m["safefield"])},
            },
            {
                "service": "light.turn_on",
                "target": {
                    "entity_id": "{{ label_entities('" + credential_key + "=" + m["label"] + "') }}",
                },
            },
            {
                "service": "rest_command.synthetic",
                "data": {
                    "url": userinfo,
                    "headers": _bearer(m["bearer"]),
                    "token_hint": _jwt_shape(m["jwt"]),
                    "query": query,
                },
            },
        ],
    }


class _AnalysisRest(SyntheticRest):
    """Adds the read-only routes the analysis families use beyond the index."""

    async def request(self, method, path, body=None, raw=False, expected_statuses=frozenset(), *, logbook_read=False):
        text = str(path)
        if method == "GET" and text.startswith("/states/"):
            entity_id = text[len("/states/"):]
            for row in self.ha.states():
                if row["entity_id"] == entity_id:
                    return deepcopy(row)
            raise HomeAssistantApiError(details={"status": 404})
        if method == "GET" and text.startswith(("/history", "/logbook", "/error")):
            return []
        return await super().request(
            method, path, body, raw, expected_statuses, logbook_read=logbook_read,
        )


class _AnalysisWebSocket(SyntheticWebSocket):
    async def command(self, payload):
        if payload.get("type") in {"trace/list", "system_log/list"}:
            return []
        return await super().command(payload)


def _serialized(value) -> str:
    def default(item):
        public = getattr(item, "public", None)
        if callable(public):
            try:
                return public()
            except TypeError:
                pass
        if hasattr(item, "__dataclass_fields__"):
            return asdict(item)
        if hasattr(item, "__dict__"):
            return vars(item)
        return str(item)

    return json.dumps(value, default=default, sort_keys=True)


class ResolvedSecretDisclosureTests(unittest.IsolatedAsyncioTestCase):
    """Review item (a): loaded configuration can hold resolved ``!secret`` values.

    One package automation exists only in loaded form (the stored route returns
    404), and one stored automation has the same content. Every analysis family
    that reads the index runs at its most detailed level. No planted value may
    appear in any output.
    """

    async def asyncSetUp(self):
        self.ha = SyntheticHomeAssistant([
            record(
                "automation.synthetic_package", None,
                automation_id="synthetic_package",
                loaded=_secret_bearing_config("synthetic_package"),
            ),
            record("automation.synthetic_stored", _secret_bearing_config("synthetic_stored")),
        ])
        self.rest = _AnalysisRest(self.ha)
        self.websocket = _AnalysisWebSocket(self.ha)
        self.index = DependencyIndex(
            DirectHaDependencyProvider(self.rest, self.websocket, secret=SECRET)
        )

    def assert_no_marker(self, label: str, output) -> str:
        text = _serialized(output)
        leaked = sorted(name for name, marker in _MARKERS.items() if marker in text)
        self.assertEqual(leaked, [], f"{label} disclosed planted values")
        return text

    async def test_no_analysis_output_carries_a_resolved_secret(self):
        snapshot, _, _ = await self.index.get(refresh=True)
        profile = snapshot.build_profile
        self.assertEqual(profile["automation_config_source_counts"]["loaded_websocket"], 2)
        self.assertEqual(self.ha.rest_counts["automation_config_read"], 0)

        outputs: dict[str, object] = {}
        dependency = EntityDependencyAnalysisService(self.index)
        for entity_id in (
            "light.synthetic_kitchen", "light.synthetic_hall", "input_number.synthetic_setpoint",
            "binary_sensor.synthetic_door", "input_text.synthetic_target",
        ):
            outputs[f"dependency {entity_id}"] = await dependency.analyze(
                entity_id=entity_id, detail_level="evidence", include_indirect=True,
            )
        impact = ChangeImpactAnalysisService(DirectHaImpactProvider(
            self.index, self.rest, self.websocket, secret=SECRET, ha_token=SECRET,
        ))
        for entity_id in ("light.synthetic_kitchen", "input_number.synthetic_setpoint"):
            outputs[f"impact {entity_id}"] = await impact.analyze(
                entity_id=entity_id, operation="remove_entity", detail_level="evidence",
            )
        integrity = ConfigurationIntegrityAnalysisService(DirectHaIntegrityProvider(
            self.index, self.rest, self.websocket, secret=SECRET, ha_token=SECRET,
        ))
        outputs["integrity"] = await integrity.analyze(detail_level="evidence", limit=100)
        reliability_provider = DirectHaReliabilityProvider(
            self.rest, self.websocket, secret=SECRET, ha_token=SECRET,
        )
        outputs["reliability"] = await AutomationReliabilityAnalysisService(
            reliability_provider
        ).analyze(automation_id="synthetic_stored", detail_level="evidence")
        incident = IncidentCorrelationService(DirectHaIncidentProvider(
            self.index, self.rest, self.websocket, reliability_provider,
            secret=SECRET, ha_token=SECRET,
        ))
        incident_values = {
            "related_entity_ids": [], "lookback_hours": 24, "correlation_window_minutes": 30,
            "trace_limit": 10, "include_dependency_context": True,
            "include_integrity_context": True, "include_reliability_context": True,
            "detail_level": "evidence", "limit": 50, "cursor": "", "refresh_index": False,
        }
        outputs["incident focus"] = await incident.analyze(
            focus_entity_id="light.synthetic_kitchen", automation_id="", **incident_values,
        )
        outputs["incident automation"] = await incident.analyze(
            focus_entity_id="", automation_id="synthetic_package", **incident_values,
        )
        handoff = HandoffGenerationService(EngineeringHandoffProvider(
            governance=SimpleNamespace(), incident=incident, dependency_index=self.index,
            rest_client=self.rest, health=SimpleNamespace(), secret=SECRET, ha_token=SECRET,
        ))
        handoff_values = {
            "handoff_type": "focused_review", "title": "",
            "focus_entity_ids": ["light.synthetic_kitchen"],
            "automation_ids": ["synthetic_package", "synthetic_stored"], "change_plan_ids": [],
            "lookback_hours": 168, "include_runtime_health": False,
            "include_governance_context": False, "include_dependency_context": True,
            "include_integrity_context": True, "include_reliability_context": False,
            "include_incident_context": True, "include_recommendations": True,
            "detail_level": "evidence", "limit": 50, "cursor": "", "refresh_index": False,
        }
        for output_format in ("structured", "markdown"):
            outputs[f"handoff {output_format}"] = await handoff.generate(
                **handoff_values, output_format=output_format,
            )
        helper = HelperDependencyRiskService(self.index)
        for entity_id in ("input_number.synthetic_setpoint", "input_select.synthetic_mode"):
            outputs[f"helper plan {entity_id}"] = await helper.assess(entity_id, refresh=True)
        outputs["entity-metadata plan"] = build_consumer_disclosure(
            snapshot,
            entity_id="light.synthetic_hall",
            current={"entity_id": "light.synthetic_hall", "labels": [], "area_id": None},
            final={"entity_id": "light.synthetic_hall", "labels": ["synthetic_downstairs"], "area_id": None},
            reference_definitions={"labels": {"synthetic_downstairs": {"name": "Synthetic downstairs"}}},
        )

        texts = {label: self.assert_no_marker(label, output) for label, output in outputs.items()}
        # The outputs are not vacuous: the package automation is analyzed, and
        # the effect projection shows the same actions with the planted values
        # replaced by digests while ordinary values stay readable.
        for label in ("dependency light.synthetic_kitchen", "impact light.synthetic_kitchen",
                      "incident automation", "handoff structured", "helper plan input_number.synthetic_setpoint"):
            self.assertIn("synthetic_package", texts[label], label)
        helper_text = texts["helper plan input_number.synthetic_setpoint"]
        self.assertIn("action[0].data.brightness=10", helper_text)
        self.assertIn("action[2].target.entity_id|entity_id|light.synthetic_hall", helper_text)
        for token in (
            "action[2].target.device_id|device_id|redacted_sha256:",
            "action[2].target.label_id|label_id|redacted_sha256:",
            "action[2].data.transition=redacted_sha256:",
        ):
            self.assertIn(token, helper_text)

    def test_effect_tokens_hash_only_values_the_sanitizer_recognizes(self):
        jwt = _jwt_shape(_MARKERS["targetjwt"])
        config = {"action": [{"target": {"label_id": jwt}}]}
        targets = _display_safe_effect_tokens(
            (
                "action[0].target.entity_id|entity_id|light.synthetic_kitchen",
                # Lowercased by the projection: recognized by its original spelling.
                "action[0].target.label_id|label_id|" + jwt.lower(),
                "action[0].target.device_id|device_id|" + _bearer(_MARKERS["target"]).lower(),
                "action[0].target.area_id|area_id|sha256:" + "0" * 64,
            ),
            config, secret=SECRET, separator="|",
        )
        self.assertEqual(targets[0], "action[0].target.entity_id|entity_id|light.synthetic_kitchen")
        self.assertTrue(targets[1].startswith("action[0].target.label_id|label_id|redacted_sha256:"))
        self.assertTrue(targets[2].startswith("action[0].target.device_id|device_id|redacted_sha256:"))
        self.assertEqual(targets[3], "action[0].target.area_id|area_id|sha256:" + "0" * 64)
        data = _display_safe_effect_tokens(
            (
                "action[0].data.brightness=10",
                "action[0].data.transition=" + SECRET,
                "action[0].data.transition=" + _bearer(_MARKERS["safefield"]),
            ),
            config, secret=SECRET, separator="=",
        )
        self.assertEqual(data[0], "action[0].data.brightness=10")
        self.assertTrue(data[1].startswith("action[0].data.transition=redacted_sha256:"))
        self.assertTrue(data[2].startswith("action[0].data.transition=redacted_sha256:"))
        for token in (*targets, *data):
            self.assertFalse(any(marker in token.lower() for marker in _MARKERS.values()), token)
            self.assertNotIn(SECRET, token)


if __name__ == "__main__":
    unittest.main()
