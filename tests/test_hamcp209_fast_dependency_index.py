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
import base64
import hashlib
import hmac
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import re
import secrets
import string
import sys
import tempfile
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
    LOADED_CONFIG_UNAVAILABLE,
    MAX_PENDING_AUTOMATION_WRITES,
    PENDING_WRITE_UNRECONCILED,
    DirectHaDependencyProvider,
    _EFFECT_DISPLAY_REGISTRY_COMMANDS,
    _EffectDisplayFilter,
    _EffectDisplayRegistry,
)
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.governance.entity_metadata_disclosure import (  # noqa: E402
    build_consumer_disclosure,
)
from ha_mcp_engineering.governance.helper_dependency import HelperDependencyRiskService  # noqa: E402
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    effect_digest_key,
    withheld_effect_digest_key,
)
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
    SYNTHETIC_DEVICE_ID,
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


async def stored_reference_scan(ha: SyntheticHomeAssistant):
    """The pre-HAMCP-209 stored-only build, used as the parity reference.

    A client with no batch read has no loaded source: every automation reads
    stored configuration with reason ``loaded_source_unsupported``, exactly
    the pre-HAMCP-209 path (``test_no_batch_client_reads_stored_as_before``).
    """

    return await provider_for(ha, batch=False).scan()


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
            stored = await stored_reference_scan(ha_stored)
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
        self.assertEqual(operations["loaded_config_batch"], 1)
        # /config, /states, entity registry, the display-only service
        # registry, one batch.
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
        # Blueprint and unavailable reads are exact; for the unreadable and
        # the mismatched loaded configuration what runs is unverified (M1).
        automation = coverage_of(result)
        self.assertEqual(automation.completeness, "partial")
        self.assertEqual(automation.failed_item_count, 3)  # + the id-less one
        unverified = sorted(
            item.source_entity_id
            for item in result.obligations
            if item.reason_code == LOADED_CONFIG_UNAVAILABLE
        )
        self.assertEqual(
            unverified,
            ["automation.synthetic_bulk_0000", "automation.synthetic_bulk_0001"],
        )
        self.assertEqual(result.profile[LOADED_CONFIG_UNAVAILABLE], 2)
        self.assertIn(LOADED_CONFIG_UNAVAILABLE, result.profile["automation_partial_reasons"])

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
                self.assertEqual(coverage_of(result).completeness, "partial")
                self.assertEqual(result.profile[LOADED_CONFIG_UNAVAILABLE], 1)
        for override in ((False, "unknown_error"), (True, {"automation_blueprint": "x"}), (True, [])):
            with self.subTest(search=override):
                ha = SyntheticHomeAssistant(generated_automations(2))
                ha.batch_overrides[("search/related", "automation.synthetic_bulk_0001")] = override
                result = await provider_for(ha).scan()
                self.assertEqual(
                    result.profile["stored_config_fallback_reasons"],
                    {"loaded_config_unreadable": 1},
                )
                self.assertEqual(coverage_of(result).completeness, "partial")

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
                self.assertTrue(any(item.startswith(LOADED_CONFIG_UNAVAILABLE)
                                    for item in automation.warnings))
                self.assertFalse(automation.fallback_occurred)
                self.assertEqual(ha.rest_counts["automation_config_read"], 4)
                # M1: stored reads stand in for unreadable loaded configuration.
                self.assertEqual(automation.completeness, "partial")
                self.assertEqual(automation.failed_item_count, 4)
                self.assertEqual(result.profile[LOADED_CONFIG_UNAVAILABLE], 4)

    async def test_incomplete_batch_result_is_a_whole_source_failure(self):
        ha = SyntheticHomeAssistant(generated_automations(3))
        original = ha.websocket.command_batch

        async def short(payloads):
            return (await original(payloads))[:-1]

        ha.websocket.command_batch = short
        result = await provider_for(ha).scan()
        self.assertEqual(result.profile["stored_config_fallback_reasons"], {"loaded_source_unavailable": 3})
        self.assertEqual(coverage_of(result).completeness, "partial")
        self.assertEqual(result.profile[LOADED_CONFIG_UNAVAILABLE], 3)

    async def test_websocket_failure_is_partial_and_loaded_only_dependency_unverified(self):
        """Reviewer M1 reproduction: stored differs from what is loaded."""

        for failure in (TimeoutError(), HomeAssistantUnavailableError()):
            with self.subTest(failure=type(failure).__name__):
                ha = SyntheticHomeAssistant(generated_automations(1))
                loaded = ha.automations["automation.synthetic_bulk_0000"]["loaded"]
                loaded["action"][0]["target"]["entity_id"] = "light.synthetic_loaded_only"
                ha.batch_failure = failure
                result = await provider_for(ha).scan()

                automation = coverage_of(result)
                self.assertEqual(automation.completeness, "partial")
                self.assertEqual(automation.failed_item_count, 1)
                self.assertNotIn(
                    "light.synthetic_loaded_only",
                    {item.target_entity_id for item in result.findings},
                )
                # The automation whose running dependency is unseen is named.
                self.assertEqual(
                    [
                        (item.source_entity_id, item.reason_code)
                        for item in result.obligations
                        if item.outcome == "coverage_failure"
                    ],
                    [("automation.synthetic_bulk_0000", LOADED_CONFIG_UNAVAILABLE)],
                )
                self.assertEqual(result.profile["automation_partial_reasons"], [LOADED_CONFIG_UNAVAILABLE])

                index = DependencyIndex(_FixedProvider(result))
                snapshot, _, _ = await index.get(refresh=True)
                health = index.health()
                self.assertEqual(
                    health["last_build_profile"]["automation_partial_reasons"],
                    [LOADED_CONFIG_UNAVAILABLE],
                )
                self.assertEqual(health["last_build_profile"][LOADED_CONFIG_UNAVAILABLE], 1)
                self.assertEqual(
                    next(item for item in snapshot.coverage if item.source_type == "automation").completeness,
                    "partial",
                )

    async def test_stable_reasons_stay_complete(self):
        """Parity-equivalent stored reads (blueprint, unavailable, id-less) are not M1."""

        ha = SyntheticHomeAssistant(representative_automations())
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            result = await provider_for(ha).scan()
        self.assertEqual(result.profile[LOADED_CONFIG_UNAVAILABLE], 0)
        self.assertNotIn(LOADED_CONFIG_UNAVAILABLE, result.profile["automation_partial_reasons"])
        self.assertEqual(coverage_of(result).failed_item_count, 1)  # id-less only

    async def test_no_batch_client_reads_stored_as_before(self):
        """No batch read means no loaded source: the pre-HAMCP-209 path, reported."""

        ha = SyntheticHomeAssistant(generated_automations(3))
        result = await provider_for(ha, batch=False).scan()
        automation = coverage_of(result)
        self.assertEqual(automation.completeness, "complete")
        self.assertEqual(result.profile[LOADED_CONFIG_UNAVAILABLE], 0)
        self.assertEqual(result.profile["stored_config_fallback_reasons"], {"loaded_source_unsupported": 3})
        self.assertEqual(result.profile["loaded_config_connection_count"], 0)
        self.assertEqual(result.profile["request_count_by_operation"]["automation_config"], 3)
        self.assertTrue(any(w.startswith("loaded_source_unsupported:") for w in automation.warnings))

    async def test_batch_failure_is_not_treated_as_unsupported(self):
        """A batch read that exists but raises is a failed loaded source (M1)."""

        ha = SyntheticHomeAssistant(generated_automations(3))

        async def fails(payloads):
            raise AttributeError("synthetic failure inside the batch")

        ha.websocket.command_batch = fails
        result = await provider_for(ha).scan()
        self.assertEqual(coverage_of(result).completeness, "partial")
        self.assertEqual(result.profile["stored_config_fallback_reasons"], {"loaded_source_unavailable": 3})
        self.assertEqual(result.profile[LOADED_CONFIG_UNAVAILABLE], 3)


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
        stored = await stored_reference_scan(ha_stored)
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
            ("synthetic_new", PENDING_WRITE_UNRECONCILED),
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
        self.assertEqual(result.profile["pending_write_journal"]["unreconciled"], 1)
        self.assertIn(PENDING_WRITE_UNRECONCILED, result.profile["automation_partial_reasons"])

        # Still unreconciled on the next build, until a read accounts for it.
        result = await provider.scan()
        self.assertEqual(coverage_of(result).completeness, "partial")
        self.assertIn(PENDING_WRITE_UNRECONCILED, result.profile["automation_partial_reasons"])

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

    async def test_journal_overflow_is_sticky_and_always_partial(self):
        ha = SyntheticHomeAssistant(generated_automations(3))
        provider = provider_for(ha)
        for index in range(MAX_PENDING_AUTOMATION_WRITES + 1):
            provider.note_automation_write(f"synthetic_written_{index}")
        self.assertTrue(provider._pending_write_overflow)
        self.assertEqual(len(provider._pending_writes), MAX_PENDING_AUTOMATION_WRITES)

        for build in range(2):
            with self.subTest(build=build):
                ha.rest_counts.clear()
                result = await provider.scan()
                automation = coverage_of(result)
                journal = result.profile["pending_write_journal"]
                self.assertEqual(automation.completeness, "partial")
                self.assertTrue(journal["overflow_at_build"])
                self.assertEqual(journal["overflow_write_count"], 1)
                self.assertIn(PENDING_WRITE_UNRECONCILED, result.profile["automation_partial_reasons"])
                self.assertTrue(any("journal overflowed" in item for item in automation.warnings))
                # Every candidate is compared with its stored form.
                self.assertEqual(ha.rest_counts["automation_config_read"], 3 + (
                    MAX_PENDING_AUTOMATION_WRITES if build == 0 else 0
                ))
                self.assertTrue(provider._pending_write_overflow)
        # The tracked identities were 404 and are cleared; the overflow is not.
        self.assertEqual(provider._pending_writes, {})

    async def test_overflowed_unloaded_write_stays_partial(self):
        """Reviewer M2 reproduction: the overflowed write is never forgotten."""

        ha = SyntheticHomeAssistant(generated_automations(2))
        provider = provider_for(ha)
        for index in range(MAX_PENDING_AUTOMATION_WRITES):
            provider.note_automation_write(f"synthetic_deleted_{index}")
        provider.note_automation_write("synthetic_overflow_unloaded")
        await ha.rest.request(
            "POST", "/config/automation/config/synthetic_overflow_unloaded",
            body={
                "id": "synthetic_overflow_unloaded",
                "trigger": [],
                "action": [{"service": "light.turn_on",
                            "target": {"entity_id": "light.synthetic_overflow_target"}}],
            },
        )
        self.assertNotIn("synthetic_overflow_unloaded", provider._pending_writes)

        for build in range(2):
            with self.subTest(build=build):
                result = await provider.scan()
                automation = coverage_of(result)
                self.assertEqual(automation.completeness, "partial")
                self.assertIn(
                    ("dependency_provider_pending_writes", PENDING_WRITE_UNRECONCILED),
                    [(item.source_id, item.reason_code) for item in result.obligations],
                )
                self.assertTrue(result.profile["pending_write_journal"]["overflow_at_build"])
        # The 256 deletions were reconciled by 404; only the overflow remains.
        self.assertEqual(provider._pending_writes, {})

        # A clean rewrite of another automation does not clear the overflow.
        provider.note_automation_write("synthetic_bulk_0000")
        result = await provider.scan()
        self.assertEqual(coverage_of(result).completeness, "partial")
        self.assertTrue(provider._pending_write_overflow)

    async def test_overflow_routed_package_automation_keeps_loaded_config(self):
        """A stored 404 under overflow alone does not lose the loaded analysis."""

        ha = SyntheticHomeAssistant(generated_automations(2))
        ha.automations["automation.synthetic_bulk_0001"]["stored"] = None
        provider = provider_for(ha)
        for index in range(MAX_PENDING_AUTOMATION_WRITES + 1):
            provider.note_automation_write(f"synthetic_written_{index}")
        result = await provider.scan()
        self.assertEqual(result.automation_read_failures, [])
        self.assertIn(
            "synthetic_bulk_0001",
            {item.source_id for item in result.findings},
        )
        self.assertEqual(coverage_of(result).completeness, "partial")
        self.assertEqual(
            [item.reason_code for item in result.obligations if item.outcome == "coverage_failure"],
            [PENDING_WRITE_UNRECONCILED],
        )

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
                # A correct client always has min(window, unanswered)
                # commands in flight here: wait for all of them under a
                # generous hang guard, so a late command on a loaded host
                # can never leave nothing to answer (HAMCP-208).
                while len(pending) < min(BATCH_WINDOW, 40 - answered):
                    pending.append(await asyncio.wait_for(ws.receive_json(), 30))
                # A window-exceeding client would already have sent one more;
                # this short probe can only miss such a bug, never fail.
                try:
                    pending.append(await asyncio.wait_for(ws.receive_json(), 0.05))
                except asyncio.TimeoutError:
                    pass
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
        # Exactly the full window was pipelined, never more.
        self.assertEqual(peer.max_in_flight, BATCH_WINDOW)
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


def _planted_values() -> dict[str, str]:
    """Secret-shaped values the sanitizer does not all recognize, built at runtime.

    Each is rejected by construction (character set, case provenance, or
    piece length), never by chance, so the assertions are deterministic.
    """

    jwt = _jwt_shape(_MARKERS["targetjwt"])
    random_text = secrets.token_hex(16)
    return {
        "jwt": jwt,
        "jwt_hash_prefixed": "sha256:" + jwt,
        "jwt_delimited": jwt + "|ordinary",
        "jwt_lowercased": jwt.lower(),
        "random_hex": random_text,
        "random_base62": "Q" + "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(32)
        ),
        "random_letters": "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(40)),
        "url_credentials": "https://" + "synthetic" + ":" + random_text + "@example.invalid/hook",
        "base64_blob": base64.b64encode(secrets.token_bytes(48)).decode("ascii"),
        "fake_digest": "sha256:" + random_text * 2,
    }


def _leaks(token: str, planted: dict[str, str]) -> list[str]:
    """Planted values, or any long fragment of one, present in ``token``."""

    folded = token.lower()
    leaked = set()
    for name, value in planted.items():
        fragments = [
            piece for piece in re.split(r"[^a-z0-9]+", value.lower()) if len(piece) >= 12
        ]
        if value.lower() in folded or any(piece in folded for piece in fragments):
            leaked.add(name)
    return sorted(leaked)


def _generated_identifier() -> str:
    """Reviewer's generator: lowercase, identifier-shaped, >159 random bits."""

    consonants = "bcdfghjklmnpqrstvwxyz"
    return "_".join(
        "".join(secrets.choice(consonants) for _ in range(4)) + secrets.choice("aeiou")
        for _ in range(8)
    )


def _generated_leaks(text: str, value: str) -> bool:
    """The value, or any two adjacent chunks of it, appear in ``text``."""

    chunks = value.split("_")
    return value in text or any(
        "_".join(chunks[index:index + 2]) in text for index in range(len(chunks) - 1)
    )


# A dictionary-like lowercase literal that no registry holds.
_UNREGISTERED_WORDS = "porch_lantern_glow"


def _display_registry(
    *, entities=(), devices=(), areas=(), floors=(), labels=(),
    services=None, domains=(), complete=True, withheld_fields=None,
):
    members = {
        "entity": frozenset(entities), "device": frozenset(devices),
        "area": frozenset(areas), "floor": frozenset(floors), "label": frozenset(labels),
    }
    if withheld_fields is None and services is not None:
        withheld_fields = {service: frozenset() for service in services}
    return _EffectDisplayRegistry(
        members=members,
        complete={kind: complete for kind in members},
        services=services,
        domains=frozenset(domains),
        sources={},
        withheld_fields=withheld_fields,
    )


_ORDINARY_REGISTRY = dict(
    entities={"light.synthetic_kitchen", "climate.synthetic_hall"},
    devices={SYNTHETIC_DEVICE_ID},
    areas={"synthetic_living_room"},
    floors={"synthetic_ground"},
    labels={"synthetic_downstairs"},
    services={
        "light.turn_on": frozenset({"brightness", "transition"}),
        "climate.set_hvac_mode": frozenset({"hvac_mode"}),
        "rest_command.synthetic": frozenset(),
    },
    domains={"light", "climate", "rest_command"},
)


class EffectDisplayAllowlistTests(unittest.TestCase):
    """Reviewer H1 residual: only registry members, numbers and enums are verbatim."""

    def filter_for(self, config, **registry):
        values = {**_ORDINARY_REGISTRY, **registry}
        return _EffectDisplayFilter(
            config, secret=SECRET, registry=_display_registry(**values),
            digest_key=effect_digest_key(SECRET),
            withheld_digest_key=withheld_effect_digest_key(),
        )

    def assert_digested(self, token, value):
        self.assertNotIn(value, token)
        self.assertIn("redacted_sha256:", token)

    def test_unregistered_values_are_digested_in_every_position(self):
        planted = {
            **_planted_values(),
            "generated_lowercase": _generated_identifier(),
            "dictionary_lowercase": _UNREGISTERED_WORDS,
        }
        for name, value in planted.items():
            with self.subTest(case=name):
                folded = value.lower()
                config = {"action": [
                    {"service": "light.turn_on", "target": {
                        "label_id": value, "area_id": folded, "device_id": folded,
                        "floor_id": folded}},
                    {"service": "rest_command." + folded, "data": {value: 7}},
                    {"service": "light.turn_on", "data": {"transition": value, folded: 7}},
                    {"service": folded + ".turn_on", "target": {"entity_id": "light." + folded}},
                    {"service": "rest_command.synthetic", "data": {folded: 7}},
                ]}
                display = self.filter_for(config)
                tokens = [
                    display.target("action[0].target.label_id|label_id|" + folded),
                    display.target("action[0].target.label_id|label_id|" + value),
                    display.target("action[0].target.area_id|area_id|" + folded),
                    display.target("action[0].target.device_id|device_id|" + folded),
                    display.target("action[0].target.floor_id|floor_id|" + folded),
                    display.target("action[3].target.entity_id|entity_id|light." + folded),
                    display.target("action[0].target.label_id|" + folded + "|x"),
                    display.target(folded + "|label_id|x"),
                    # A registered value under a path that is not a projected step.
                    display.target(
                        "action[0].target." + folded + ".label_id|label_id|synthetic_downstairs"
                    ),
                    display.data("action[2].data.transition=" + folded),
                    display.data("action[2].data.transition=" + value),
                    display.data("action[1].data." + folded + "=7"),
                    display.data("action[2].data." + folded + "=7"),
                    display.data("action[4].data." + folded + "=7"),
                    display.data(folded),
                    display.service("rest_command." + folded),
                    display.service(folded + ".turn_on"),
                    display.service(folded),
                    display.domain(folded),
                ]
                for token in tokens:
                    self.assertEqual(_leaks(token, planted), [], token)
                    self.assertNotIn(folded, token)
                    self.assertIn("redacted_sha256:", token)
                self.assertFalse(display.unverified)

    def test_registered_values_stay_verbatim(self):
        config = {"action": [
            {"service": "light.turn_on",
             "target": {"entity_id": ["light.synthetic_kitchen", "all"],
                        "area_id": "synthetic_living_room",
                        "floor_id": "synthetic_ground",
                        "label_id": "synthetic_downstairs",
                        "device_id": SYNTHETIC_DEVICE_ID},
             "data": {"brightness": 10, "transition": 2.5}},
            {"service": "climate.set_hvac_mode",
             "target": {"entity_id": "climate.synthetic_hall"},
             "data": {"hvac_mode": "heat_cool"}},
        ]}
        display = self.filter_for(config)
        for token in (
            "action[0].target.entity_id[0]|entity_id|light.synthetic_kitchen",
            "action[0].target.entity_id[1]|entity_id|all",
            "action[0].target.area_id|area_id|synthetic_living_room",
            "action[0].target.floor_id|floor_id|synthetic_ground",
            "action[0].target.label_id|label_id|synthetic_downstairs",
            "action[0].target.device_id|device_id|" + SYNTHETIC_DEVICE_ID,
            "action[1].target.entity_id|entity_id|climate.synthetic_hall",
            "action[0].target.entity_id|entity_id|sha256:" + "0" * 64,
        ):
            with self.subTest(token=token):
                self.assertEqual(display.target(token), token)
        self.assertTrue(display.target(
            "action[0].event|event_type|redacted_sha256:" + "1" * 64
        ).startswith("action[0].event|event_type|redacted_sha256:"))
        for token in (
            "action[0].data.brightness=10",
            "action[0].data.transition=2.5",
            "action[0].data.transition=true",
            "action[1].data.hvac_mode=heat_cool",
            "action[0].data.brightness=dynamic_sha256:" + "a" * 64,
            "overflow_sha256:" + "b" * 64,
        ):
            with self.subTest(token=token):
                self.assertEqual(display.data(token), token)
        self.assertEqual(display.service("light.turn_on"), "light.turn_on")
        self.assertEqual(display.service("climate.set_hvac_mode"), "climate.set_hvac_mode")
        self.assertEqual(display.domain("light"), "light")
        self.assertFalse(display.unverified)

    def test_each_position_requires_registry_membership(self):
        """A registered value of one kind does not admit another position."""

        config = {"action": [
            {"service": "light.turn_on",
             "target": {"label_id": "synthetic_living_room", "area_id": "synthetic_downstairs",
                        "device_id": "synthetic_ground", "floor_id": SYNTHETIC_DEVICE_ID,
                        "entity_id": "light.synthetic_unregistered"},
             "data": {"hvac_mode": "heat", "brightness": 10}},
        ]}
        display = self.filter_for(config)
        for token, value in (
            ("action[0].target.label_id|label_id|synthetic_living_room", "synthetic_living_room"),
            ("action[0].target.area_id|area_id|synthetic_downstairs", "synthetic_downstairs"),
            ("action[0].target.device_id|device_id|synthetic_ground", "synthetic_ground"),
            ("action[0].target.floor_id|floor_id|" + SYNTHETIC_DEVICE_ID, SYNTHETIC_DEVICE_ID),
            ("action[0].target.entity_id|entity_id|light.synthetic_unregistered",
             "light.synthetic_unregistered"),
        ):
            with self.subTest(token=token):
                self.assert_digested(display.target(token), value)
        # A key of another service's schema is not this step's data key.
        self.assert_digested(display.data("action[0].data.hvac_mode=heat"), "hvac_mode")
        # An unregistered service of a registered domain, and the reverse.
        self.assert_digested(display.service("light.flicker"), "flicker")
        self.assert_digested(display.domain("rest_commands"), "rest_commands")
        # A data path the projection cannot emit for this configuration.
        self.assert_digested(display.data("action[7].data.brightness=10"), "action[7]")
        self.assert_digested(display.data("action[0].data.brightness.inner=10"), "inner")
        self.assert_digested(display.data("action[0].event_data.brightness=10"), "event_data")

    def test_enum_words_require_a_literal_configuration(self):
        display = self.filter_for({"action": [
            {"service": "climate.set_hvac_mode", "data": {"hvac_mode": "{{ mode }}"}},
            {"service": "light.turn_on", "target": {"entity_id": "{{ targets }}"}},
        ]})
        self.assertIn("redacted_sha256:", display.data("action[0].data.hvac_mode=heat"))
        self.assertIn(
            "redacted_sha256:", display.target("action[1].target.entity_id|entity_id|all"),
        )
        # A digest-shaped configuration string is not a generated digest.
        fake = "sha256:" + "c" * 64
        display = self.filter_for({"action": [{"target": {"label_id": fake}}]})
        self.assertIn(
            "|label_id|redacted_sha256:",
            display.target("action[0].target.label_id|label_id|" + fake),
        )

    def test_registered_value_the_sanitizer_recognizes_is_digested(self):
        """Registry membership skips no sanitizer or known-secret check."""

        jwt = _planted_values()["jwt"]
        config = {"action": [{"service": "light.turn_on", "target": {"label_id": jwt}}]}
        display = self.filter_for(config, labels={jwt.lower()})
        self.assert_digested(display.target("action[0].target.label_id|label_id|" + jwt.lower()), jwt.lower())
        display = self.filter_for(
            {"action": [{"service": "light.turn_on", "target": {"label_id": SECRET}}]},
            labels={SECRET},
        )
        self.assert_digested(display.target("action[0].target.label_id|label_id|" + SECRET), SECRET)

    def test_known_numeric_and_enum_values_are_digested(self):
        """Reviewer M: numbers, JSON literals and enum words pass the sanitizer too."""

        number = "".join(str(digit) for digit in (4, 3, 8, 2, 7, 1, 9, 0, 5, 8, 2, 6, 3, 4, 1))
        config = {"action": [
            {"service": "light.turn_on",
             "target": {"entity_id": ["all", "light.synthetic_kitchen"]},
             "data": {"transition": number, "brightness": 10}},
            {"service": "climate.set_hvac_mode", "data": {"hvac_mode": "heat"}},
        ]}
        for known, token in (
            (number, "action[0].data.transition=" + number),
            ("heat", "action[1].data.hvac_mode=heat"),
            ("true", "action[0].data.transition=true"),
        ):
            with self.subTest(known=known):
                display = _EffectDisplayFilter(
                    config, secret=known, registry=_display_registry(**_ORDINARY_REGISTRY),
                    digest_key=effect_digest_key(known),
                    withheld_digest_key=withheld_effect_digest_key(),
                )
                shown = display.data(token)
                self.assertNotIn("=" + known, shown)
                self.assertTrue(shown.startswith(token.split("=", 1)[0] + "=redacted_sha256:"))
                self.assertEqual(display.data("action[0].data.brightness=10"),
                                 "action[0].data.brightness=10")
        display = _EffectDisplayFilter(
            config, secret="all", registry=_display_registry(**_ORDINARY_REGISTRY),
            digest_key=effect_digest_key("all"),
            withheld_digest_key=withheld_effect_digest_key(),
        )
        self.assert_digested(
            display.target("action[0].target.entity_id[0]|entity_id|all"), "|all",
        )

    def test_unavailable_registry_digests_and_marks_unverified(self):
        config = {"action": [
            {"service": "light.turn_on",
             "target": {"entity_id": "light.synthetic_kitchen", "area_id": "synthetic_living_room"},
             "data": {"brightness": 10}},
        ]}
        cases = {
            "services": dict(services=None),
            "area": dict(areas=(), complete=False),
        }
        for name, override in cases.items():
            with self.subTest(case=name):
                values = {**_ORDINARY_REGISTRY, **override}
                display = _EffectDisplayFilter(
                    config, secret=SECRET, registry=_display_registry(**values),
                    digest_key=effect_digest_key(SECRET),
                    withheld_digest_key=withheld_effect_digest_key(),
                )
                if name == "services":
                    self.assertIn("redacted_sha256:", display.service("light.turn_on"))
                    self.assertIn("redacted_sha256:", display.data("action[0].data.brightness=10"))
                else:
                    self.assertIn("redacted_sha256:", display.target(
                        "action[0].target.area_id|area_id|synthetic_living_room"))
                self.assertTrue(display.unverified)
        # An incomplete registry never shows a value it does not hold.
        display = _EffectDisplayFilter(config, secret=SECRET, registry=_display_registry(
            **{**_ORDINARY_REGISTRY, "complete": False},
        ), digest_key=effect_digest_key(SECRET),
            withheld_digest_key=withheld_effect_digest_key())
        self.assertEqual(
            display.target("action[0].target.entity_id|entity_id|light.synthetic_kitchen"),
            "action[0].target.entity_id|entity_id|light.synthetic_kitchen",
        )
        self.assertFalse(display.unverified)

    def test_service_registry_schema_is_validated_and_bounded(self):
        from ha_mcp_engineering.dependency import provider as module

        domains, services, withheld = module._service_registry_schema([
            {"domain": "light", "services": {"turn_on": {"fields": {
                "brightness": {"selector": {"number": {}}},
                "advanced_fields": {"collapsed": True, "fields": {"flash": {}}},
            }}}},
        ])
        self.assertEqual(domains, frozenset({"light"}))
        self.assertEqual(services, {"light.turn_on": frozenset({"brightness", "flash"})})
        # HAMCP-542: a field with no selector is withheld; a number selector is not.
        self.assertEqual(withheld, {"light.turn_on": frozenset({"flash"})})
        for rows in (
            {"domain": "light"},
            [{"domain": "light", "services": []}],
            [{"domain": 7, "services": {}}],
            [{"domain": "light", "services": {"turn_on": "x"}}],
            [{"domain": "light", "services": {"turn_on": {"fields": "x"}}}],
            ["light"],
        ):
            with self.subTest(rows=rows):
                self.assertIsNone(module._service_registry_schema(rows))
        with patch.object(module, "MAX_EFFECT_DISPLAY_SERVICES", 1):
            self.assertIsNone(module._service_registry_schema([
                {"domain": "light", "services": {"turn_on": {}, "turn_off": {}}},
            ]))
        with patch.object(module, "MAX_EFFECT_DISPLAY_SERVICE_FIELDS", 1):
            self.assertIsNone(module._service_registry_schema([
                {"domain": "light", "services": {"turn_on": {"fields": {"a": {}, "b": {}}}}},
            ]))
        self.assertIsNone(module._registry_identifiers([{"area_id": None}], "area_id"))
        self.assertIsNone(module._registry_identifiers({"area_id": "x"}, "area_id"))
        with patch.object(module, "MAX_EFFECT_DISPLAY_REGISTRY_ENTRIES", 1):
            self.assertIsNone(module._registry_identifiers(
                [{"area_id": "a"}, {"area_id": "b"}], "area_id",
            ))
        self.assertEqual(
            module._registry_identifiers([{"area_id": "Synthetic_Room"}], "area_id"),
            frozenset({"synthetic_room"}),
        )


class EffectDisplayScanTests(unittest.IsolatedAsyncioTestCase):
    """Every projection position of a real scan goes through the allowlist."""

    async def test_planted_values_never_reach_a_profile_position(self):
        planted = {
            **_planted_values(),
            "generated_lowercase": _generated_identifier(),
            "dictionary_lowercase": _UNREGISTERED_WORDS,
        }
        for name, value in planted.items():
            with self.subTest(case=name):
                config = {
                    "id": "synthetic_planted",
                    "alias": "Synthetic planted",
                    "trigger": [{"platform": "state", "entity_id": "input_boolean.synthetic_gate"}],
                    "action": [
                        {"service": "light.turn_on",
                         "target": {"entity_id": "light.synthetic_kitchen", "label_id": value,
                                    "device_id": value, "area_id": value, "floor_id": value},
                         "data": {"brightness": 10, "transition": value}},
                        {"service": "rest_command." + value, "data": {value: 7}},
                        {"service": value + ".turn_on", "target": {"entity_id": value + ".synthetic"}},
                        {"service": "input_boolean.turn_on",
                         "target": {"entity_id": value}},
                    ],
                }
                ha = SyntheticHomeAssistant([
                    record("automation.synthetic_planted", None,
                           automation_id="synthetic_planted", loaded=config),
                ])
                result = await provider_for(ha).scan()
                profiles = [asdict(item) for item in result.automation_action_profiles]
                self.assertEqual(len(profiles), 1)
                profile = profiles[0]
                text = json.dumps(profiles, sort_keys=True)
                self.assertEqual(_leaks(text, planted), [], name)
                # Not vacuous: each position is populated and ordinary values remain.
                for field in ("action_domains", "services", "effect_targets", "effect_data"):
                    self.assertTrue(
                        any("redacted_sha256:" in item for item in profile[field]), (name, field),
                    )
                self.assertIn("light.turn_on", profile["services"])
                self.assertIn("light", profile["action_domains"])
                self.assertIn(
                    "action[0].target.entity_id|entity_id|light.synthetic_kitchen",
                    profile["effect_targets"],
                )
                self.assertIn("action[0].data.brightness=10", profile["effect_data"])


class EffectDisplayPlanPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """Reviewer H1 reproduction through helper-state plan creation and storage."""

    async def test_jwt_variants_are_digested_in_plan_response_and_files(self):
        from ha_mcp_engineering.governance.service import ChangeGovernanceService
        from ha_mcp_engineering.governance.storage import ChangePlanRepository
        from tests.test_beta37_exact_helper_state import (
            Clock,
            FakeHelperStateGateway,
            UnusedLegacyGateway,
        )

        planted = _planted_values()
        for name, value in planted.items():
            with self.subTest(case=name):
                gateway = FakeHelperStateGateway()
                config = {
                    "id": "synthetic_package",
                    "alias": "Synthetic package",
                    "trigger": [{"platform": "state", "entity_id": gateway.entity_id}],
                    "action": [
                        {"service": "light.turn_on",
                         "target": {"label_id": value, "entity_id": "light.synthetic_kitchen"},
                         "data": {"brightness": 10}},
                    ],
                }
                ha = SyntheticHomeAssistant([
                    record("automation.synthetic_package", None,
                           automation_id="synthetic_package", loaded=config),
                ])
                index = DependencyIndex(DirectHaDependencyProvider(
                    _AnalysisRest(ha), _AnalysisWebSocket(ha), secret=SECRET,
                ))
                with tempfile.TemporaryDirectory(prefix="hamcp209-plan-") as directory:
                    service = ChangeGovernanceService(
                        ChangePlanRepository(Path(directory) / "plans"),
                        UnusedLegacyGateway(),
                        now=Clock(),
                        helper_state_gateway=gateway,
                        helper_dependency_risk_reader=HelperDependencyRiskService(index).assess,
                        plan_observability_cursor_key=b"synthetic-plan-key" * 2,
                    )
                    result = await service.create_helper_state_plan(
                        entity_id=gateway.entity_id, desired_state="on",
                    )
                    response = _serialized(result)
                    stored = "\n".join(
                        path.read_text(encoding="utf-8")
                        for path in sorted(Path(directory).rglob("*.json"))
                    )
                self.assertTrue(stored)
                self.assertEqual(gateway.dispatch_count, 0)
                self.assertEqual(ha.rest_counts["automation_config_read"], 0)
                for label, text in (("response", response), ("plan file", stored)):
                    self.assertEqual(_leaks(text, planted), [], f"{name} in {label}")
                    # Not vacuous: the projection is present with ordinary values.
                    self.assertIn("light.synthetic_kitchen", text, label)
                    self.assertIn("action[0].data.brightness=10", text, label)
                    self.assertIn("action[0].target.label_id|label_id|redacted_sha256:", text, label)



_GENERATED_POSITIONS = (
    "label_id", "device_id", "area_id", "floor_id", "entity_id",
    "data_value", "data_key", "service", "domain",
)


def _positioned_action(position: str, value: str) -> dict:
    """The reviewer's probe: one unregistered value in one projection position."""

    action = {
        "service": "light.turn_on",
        "target": {"entity_id": "light.synthetic_kitchen"},
        "data": {"brightness": 10},
    }
    if position in ("label_id", "device_id", "area_id", "floor_id"):
        action["target"][position] = value
    elif position == "entity_id":
        action["target"]["entity_id"] = ["light.synthetic_kitchen", "light." + value]
    elif position == "data_value":
        action["data"]["transition"] = value
    elif position == "data_key":
        action["data"][value] = 7
    elif position == "service":
        action["service"] = "rest_command." + value
    elif position == "domain":
        action["service"] = value + ".turn_on"
    return action


def _single_automation(action, trigger_entity="input_boolean.synthetic_gate"):
    config = {
        "id": "synthetic_package",
        "alias": "Synthetic package",
        "trigger": [{"platform": "state", "entity_id": trigger_entity}],
        "action": [action] if isinstance(action, dict) else action,
    }
    return SyntheticHomeAssistant([
        record("automation.synthetic_package", None,
               automation_id="synthetic_package", loaded=config),
    ])


async def _helper_plan(ha, gateway, *, secret=SECRET, sensitive_values=()):
    """Create a helper-state plan; return (result, response text, plan files text)."""

    from ha_mcp_engineering.governance.service import ChangeGovernanceService
    from ha_mcp_engineering.governance.storage import ChangePlanRepository
    from tests.test_beta37_exact_helper_state import Clock, UnusedLegacyGateway

    index = DependencyIndex(DirectHaDependencyProvider(
        _AnalysisRest(ha), _AnalysisWebSocket(ha), secret=secret,
    ))
    with tempfile.TemporaryDirectory(prefix="hamcp209-plan-") as directory:
        service = ChangeGovernanceService(
            ChangePlanRepository(Path(directory) / "plans"),
            UnusedLegacyGateway(),
            now=Clock(),
            helper_state_gateway=gateway,
            helper_dependency_risk_reader=HelperDependencyRiskService(index).assess,
            sensitive_values=sensitive_values,
            plan_observability_cursor_key=b"synthetic-plan-key" * 2,
        )
        result = await service.create_helper_state_plan(
            entity_id=gateway.entity_id, desired_state="on",
        )
        stored = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(Path(directory).rglob("*.json"))
        )
    return result, _serialized(result), stored


class EffectDisplayRegistryScanTests(unittest.IsolatedAsyncioTestCase):
    """Reviewer H1 residual through a real scan and a persisted plan."""

    async def test_generated_lowercase_value_is_digested_in_every_position(self):
        from tests.test_beta37_exact_helper_state import FakeHelperStateGateway

        for value_name, value in (
            ("generated", _generated_identifier()),
            ("dictionary", _UNREGISTERED_WORDS),
        ):
            for position in _GENERATED_POSITIONS:
                with self.subTest(value=value_name, position=position):
                    gateway = FakeHelperStateGateway()
                    ha = _single_automation(
                        _positioned_action(position, value), gateway.entity_id,
                    )
                    scan = await provider_for(ha).scan()
                    profile = asdict(scan.automation_action_profiles[0])
                    scan_text = json.dumps(profile, sort_keys=True)
                    result, response, stored = await _helper_plan(ha, gateway)
                    self.assertTrue(stored)
                    self.assertEqual(gateway.dispatch_count, 0)
                    for label, text in (
                        ("scan", scan_text), ("response", response), ("plan file", stored),
                    ):
                        self.assertFalse(_generated_leaks(text, value), f"{position} in {label}")
                        self.assertIn("redacted_sha256:", text, label)
                        # Not vacuous: registered values stay readable.
                        self.assertIn(
                            "action[0].target.entity_id"
                            + ("[0]" if position == "entity_id" else "")
                            + "|entity_id|light.synthetic_kitchen",
                            text, label,
                        )
                        if position not in ("service", "domain"):
                            self.assertIn("action[0].data.brightness=10", text, label)
                            self.assertIn("light.turn_on", text, label)
                    self.assertFalse(profile["presentation_truncated"])
                    self.assertEqual(scan.profile["effect_display_unverified_profiles"], 0)

    async def test_registered_values_stay_verbatim_through_scan(self):
        ha = _single_automation([
            {"service": "light.turn_on",
             "target": {"entity_id": "light.synthetic_kitchen",
                        "area_id": "synthetic_living_room", "floor_id": "synthetic_ground",
                        "label_id": "synthetic_downstairs", "device_id": SYNTHETIC_DEVICE_ID},
             "data": {"brightness": 10, "rgb_color": [1, 2, 3]}},
            {"service": "climate.set_hvac_mode",
             "target": {"entity_id": "climate.synthetic_hall"},
             "data": {"hvac_mode": "heat_cool"}},
        ])
        scan = await provider_for(ha).scan()
        profile = asdict(scan.automation_action_profiles[0])
        for token in (
            "action[0].target.entity_id|entity_id|light.synthetic_kitchen",
            "action[0].target.area_id|area_id|synthetic_living_room",
            "action[0].target.floor_id|floor_id|synthetic_ground",
            "action[0].target.label_id|label_id|synthetic_downstairs",
            "action[0].target.device_id|device_id|" + SYNTHETIC_DEVICE_ID,
            "action[1].target.entity_id|entity_id|climate.synthetic_hall",
        ):
            self.assertIn(token, profile["effect_targets"])
        for token in (
            "action[0].data.brightness=10",
            "action[0].data.rgb_color[0]=1",
            "action[1].data.hvac_mode=heat_cool",
        ):
            self.assertIn(token, profile["effect_data"])
        self.assertEqual(set(profile["services"]), {"light.turn_on", "climate.set_hvac_mode"})
        self.assertEqual(set(profile["action_domains"]), {"light", "climate"})
        self.assertFalse(profile["presentation_truncated"])
        sources = scan.profile["effect_display_registry"]
        self.assertEqual(sources["service"]["registry_read"], "complete")
        # The label is already known from the entity registry: no extra read.
        self.assertEqual(sources["label"]["registry_read"], "not_needed")
        for kind in ("device", "area", "floor"):
            self.assertEqual(sources[kind]["registry_read"], "complete", kind)
        counts = scan.profile["request_count_by_operation"]
        self.assertEqual(counts["effect_display_service_registry"], 1)
        self.assertNotIn("effect_display_label_registry", counts)

    async def test_no_target_registry_read_when_not_needed(self):
        ha = _single_automation({
            "service": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"},
        })
        scan = await provider_for(ha).scan()
        counts = scan.profile["request_count_by_operation"]
        self.assertEqual(
            sorted(key for key in counts if key.startswith("effect_display_")),
            ["effect_display_service_registry"],
        )
        # No automation: no registry read at all.
        scan = await provider_for(SyntheticHomeAssistant([])).scan()
        self.assertFalse(any(
            key.startswith("effect_display_") for key in scan.profile["request_count_by_operation"]
        ))
        self.assertEqual(scan.profile["effect_display_registry"], {})

    async def test_unavailable_registry_fails_closed_and_is_partial(self):
        action = {
            "service": "light.turn_on",
            "target": {"entity_id": "light.synthetic_kitchen", "area_id": "synthetic_living_room"},
            "data": {"brightness": 10},
        }
        malformed = [{"domain": "light", "services": "not-a-mapping"}]
        for name, failures, overrides in (
            ("services raises", {"services"}, {}),
            ("services malformed", set(), {"services": malformed}),
            ("area raises", {"config/area_registry/list"}, {}),
            ("area malformed", set(), {"config/area_registry/list": [{"area_id": 7}]}),
        ):
            with self.subTest(case=name):
                ha = _single_automation(action)
                ha.registry_failures = failures
                ha.registry_overrides = overrides
                scan = await provider_for(ha).scan()
                profile = asdict(scan.automation_action_profiles[0])
                self.assertTrue(profile["presentation_truncated"])
                self.assertEqual(scan.profile["effect_display_unverified_profiles"], 1)
                warnings = coverage_of(scan, "entity_metadata").warnings
                self.assertIn(
                    "Home Assistant registry data for effect display was unavailable; "
                    "1 automation profile(s) show unverified values as digests.",
                    warnings,
                )
                # Registered entity targets are still shown; the scan is not failed.
                self.assertIn(
                    "action[0].target.entity_id|entity_id|light.synthetic_kitchen",
                    profile["effect_targets"],
                )
                sources = scan.profile["effect_display_registry"]
                if name.startswith("services"):
                    self.assertEqual(sources["service"]["registry_read"], "unavailable")
                    self.assertNotIn("light.turn_on", profile["services"])
                    self.assertNotIn("action[0].data.brightness=10", profile["effect_data"])
                    self.assertIn("light", profile["action_domains"])
                else:
                    self.assertEqual(sources["area"]["registry_read"], "unavailable")
                    self.assertNotIn(
                        "action[0].target.area_id|area_id|synthetic_living_room",
                        profile["effect_targets"],
                    )
                    self.assertIn("light.turn_on", profile["services"])
                self.assertEqual(coverage_of(scan).completeness, "complete")

    async def test_known_numeric_value_is_digested_and_the_plan_succeeds(self):
        """Reviewer M: a known value matching the number grammar is a digest, not a refusal."""

        from tests.test_beta37_exact_helper_state import FakeHelperStateGateway

        number = "".join(str(digit) for digit in (4, 3, 8, 2, 7, 1, 9, 0, 5, 8, 2, 6, 3, 4, 1))
        for name, data in (
            ("number", {"transition": number}),
            ("enum", {"hvac_mode": "heat"}),
        ):
            with self.subTest(case=name):
                known = number if name == "number" else "heat"
                gateway = FakeHelperStateGateway()
                service = "light.turn_on" if name == "number" else "climate.set_hvac_mode"
                ha = _single_automation({
                    "service": service,
                    "target": {"entity_id": "light.synthetic_kitchen"},
                    "data": {**data, "brightness": 10} if name == "number" else data,
                }, gateway.entity_id)
                result, response, stored = await _helper_plan(
                    ha, gateway, secret=known, sensitive_values=(known,),
                )
                self.assertEqual(gateway.dispatch_count, 0)
                self.assertTrue(stored)
                key = next(iter(data))
                for label, text in (("response", response), ("plan file", stored)):
                    self.assertNotIn(f"data.{key}={known}", text, label)
                    self.assertIn(f"action[0].data.{key}=redacted_sha256:", text, label)
                    if name == "number":
                        self.assertNotIn(known, text, label)
                        self.assertIn("action[0].data.brightness=10", text, label)
                self.assertNotEqual(result.get("status"), "refused")


class RealHomeAssistantReadCaptureTests(unittest.IsolatedAsyncioTestCase):
    """The disposable Real HA lane's read capture admits the display reads.

    ``scripts/real_ha_contract_tests.py`` asserts that the dependency feature
    raised no capture violation. The effect-display registry reads are
    read-only list commands; without them the lane failed on afaff67 with
    ``script_stage=read_capture_verification`` while the provider correctly
    failed closed.
    """

    async def test_scan_through_the_capture_has_no_violation(self):
        from scripts.real_ha_contract_tests import _ScriptDependencyReadCapture

        # The lane's capture has no ``command_batch``: the stored path.
        ha = SyntheticHomeAssistant([
            record("automation.synthetic_stored", {
                "id": "synthetic_stored",
                "alias": "Synthetic stored",
                "trigger": [{"platform": "state", "entity_id": "input_boolean.synthetic_gate"}],
                "action": [{
                    "service": "light.turn_on",
                    "target": {"entity_id": "light.synthetic_kitchen",
                               "area_id": "synthetic_living_room",
                               "floor_id": "synthetic_ground",
                               "label_id": "synthetic_downstairs",
                               "device_id": SYNTHETIC_DEVICE_ID},
                    "data": {"brightness": 10},
                }],
            }),
        ])
        capture = _ScriptDependencyReadCapture(
            ha.rest, ha.websocket_without_batch, SimpleNamespace(execute_read=None),
        )
        scan = await DirectHaDependencyProvider(capture, capture, secret=SECRET).scan()
        self.assertEqual(capture.violations, [])
        commands = {call[1] for call in capture.calls if call[0] == "websocket"}
        for command, _key, _operation in _EFFECT_DISPLAY_REGISTRY_COMMANDS.values():
            if command != "config/label_registry/list":
                self.assertIn(command, commands)
        profile = asdict(scan.automation_action_profiles[0])
        self.assertFalse(profile["presentation_truncated"])
        self.assertIn(
            "action[0].target.area_id|area_id|synthetic_living_room", profile["effect_targets"],
        )

    async def test_capture_admits_exactly_the_reviewed_reads(self):
        from scripts.real_ha_contract_tests import _ScriptDependencyReadCapture

        reviewed = {"config/entity_registry/list"} | {
            command for command, _key, _operation in _EFFECT_DISPLAY_REGISTRY_COMMANDS.values()
        }
        self.assertEqual(_ScriptDependencyReadCapture.REVIEWED_WEBSOCKET_COMMANDS, reviewed)
        ha = SyntheticHomeAssistant([])
        capture = _ScriptDependencyReadCapture(
            ha.rest, ha.websocket_without_batch, SimpleNamespace(execute_read=None),
        )
        for payload in (
            {"type": "config/entity_registry/update"},
            {"type": "config/device_registry/update"},
            {"type": "config/area_registry/delete"},
            {"type": "call_service"},
            {"type": "automation/config", "entity_id": "automation.synthetic"},
        ):
            with self.subTest(command=payload["type"]):
                with self.assertRaises(AssertionError):
                    await capture.command(payload)
        with self.assertRaises(AssertionError):
            await capture.request("POST", "/services/automation/reload", {})
        self.assertEqual(len(capture.violations), 6)
        self.assertEqual(capture.calls, [])


# HAMCP-542: credential values in the effect projection.  Synthetic codes
# only; each is unmistakable and none is a real credential.
_CODES = {
    "alarm code int": 860317,
    "alarm code str": "552907",
    "lock code int": 731946,
    "pin int (number selector)": 470219,
    "pin str (number selector)": "470220",
    "passcode (password selector)": 615283,
    "keypad entry (password selector)": 615284,
    "notify top-level code": 284615,
    "notify nested pin": 931842,
    "script field code": 118204,
    "script variables code": 118205,
    "blueprint input code": 860318,
    "keypad code (number selector)": 470221,
    # Not a credential name or a free-form field: still digested, so the
    # digest must be keyed (a plain sha256 of a short string is recovered).
    "string under a select field": "552911",
}
# Ordinary numbers stay readable where they are today.
_VERBATIM_TOKENS = (
    "action[7].data.value=39177",
    "action[8].data.value=21.75",
    "action[9].data.value=42",
    "action[10].data.brightness=10",
    "action[11].data.zip_code=73301",
    "action[11].data.country_code=49",
    "action[11].data.color_code=255",
)
_WITHHELD_TOKENS = (
    "action[0].data.code=withheld",
    "action[1].data.code=withheld",
    "action[2].data.code=withheld",
    "action[3].data.pin=withheld",
    "action[4].data.pin=withheld",
    "action[5].data.passcode=withheld",
    "action[5].data.keypad_entry=withheld",
    "action[6].data.code=withheld",
    "action[12].data.code=withheld",
    "action[14].data.code=withheld",
)


def _hamcp542_fields(kind, *keys, options=None):
    return {key: {"selector": {kind: options}} for key in keys}


def _hamcp542_services(base):
    """Core 2026.11.3 (92da9f15) selectors for the services the codes use.

    alarm_control_panel ``code`` and lock ``code`` are ``text`` selectors in
    their ``services.yaml``; script ``turn_on`` ``variables`` is ``object``.
    The keypad and weather services are synthetic: the keypad ``code`` uses
    a ``number`` selector so the credential leaf rule is the only guard.
    """

    return [
        *base(),
        {"domain": "alarm_control_panel", "services": {
            "alarm_disarm": {"fields": _hamcp542_fields("text", "code")},
            "alarm_arm_away": {"fields": _hamcp542_fields("text", "code")},
        }},
        {"domain": "lock", "services": {"unlock": {"fields": _hamcp542_fields("text", "code")}}},
        {"domain": "synthetic_keypad", "services": {"enter": {"fields": {
            **_hamcp542_fields("number", "pin", "code", options={}),
            **_hamcp542_fields("text", "passcode", "keypad_entry", options={"type": "password"}),
        }}}},
        {"domain": "notify", "services": {"synthetic_phone": {"fields": {
            **_hamcp542_fields("text", "message", "title", "code"),
            **_hamcp542_fields("object", "data"),
        }}}},
        {"domain": "synthetic_weather", "services": {"lookup": {"fields": _hamcp542_fields(
            "number", "zip_code", "country_code", "color_code", options={},
        )}}},
        {"domain": "script", "services": {
            "turn_on": {"fields": _hamcp542_fields("object", "variables")},
            "synthetic_disarm": {"fields": _hamcp542_fields("text", "code")},
        }},
    ]


def _hamcp542_actions(codes):
    panel = {"entity_id": "alarm_control_panel.synthetic_panel"}
    setpoint = {"entity_id": "input_number.synthetic_setpoint"}
    return [
        {"service": "alarm_control_panel.alarm_disarm", "target": panel,
         "data": {"code": codes["alarm code int"]}},
        {"service": "alarm_control_panel.alarm_arm_away", "target": panel,
         "data": {"code": codes["alarm code str"]}},
        {"service": "lock.unlock", "target": {"entity_id": "lock.synthetic_front"},
         "data": {"code": codes["lock code int"]}},
        {"service": "synthetic_keypad.enter", "data": {"pin": codes["pin int (number selector)"]}},
        {"service": "synthetic_keypad.enter", "data": {"pin": codes["pin str (number selector)"]}},
        {"service": "synthetic_keypad.enter",
         "data": {"passcode": codes["passcode (password selector)"],
                  "keypad_entry": codes["keypad entry (password selector)"]}},
        {"service": "notify.synthetic_phone",
         "data": {"message": "synthetic", "code": codes["notify top-level code"],
                  "data": {"pin": codes["notify nested pin"]}}},
        {"service": "input_number.set_value", "target": setpoint, "data": {"value": 39177}},
        {"service": "input_number.set_value", "target": setpoint, "data": {"value": 21.75}},
        {"service": "number.set_value", "target": {"entity_id": "number.synthetic_level"},
         "data": {"value": 42}},
        {"service": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"},
         "data": {"brightness": 10}},
        {"service": "synthetic_weather.lookup",
         "data": {"zip_code": 73301, "country_code": 49, "color_code": 255}},
        {"service": "script.synthetic_disarm", "data": {"code": codes["script field code"]}},
        {"service": "script.turn_on", "target": {"entity_id": "script.synthetic_disarm"},
         "data": {"variables": {"code": codes["script variables code"]}}},
        {"service": "synthetic_keypad.enter", "data": {"code": codes["keypad code (number selector)"]}},
        {"service": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"},
         "data": {"flash": codes["string under a select field"]}},
    ]


def _hamcp542_blueprint(path):
    if path != "synthetic/keypad_disarm.yaml":
        return None, "blueprint_source_unavailable"
    return {
        "blueprint": {"name": "Synthetic keypad disarm", "domain": "automation",
                      "input": {"gate": {}, "alarm_code": {}}},
        "trigger": [{"platform": "state", "entity_id": {"__blueprint_input__": "gate"}}],
        "action": [{"service": "alarm_control_panel.alarm_disarm",
                    "target": {"entity_id": "alarm_control_panel.synthetic_panel"},
                    "data": {"code": {"__blueprint_input__": "alarm_code"}}}],
    }, None


def _hamcp542_home(gate, codes=None):
    codes = dict(codes or _CODES)
    blueprint_input = {"gate": gate, "alarm_code": codes["blueprint input code"]}
    ha = SyntheticHomeAssistant([
        record("automation.synthetic_keypad", None, automation_id="synthetic_keypad", loaded={
            "id": "synthetic_keypad", "alias": "Synthetic keypad",
            "trigger": [{"platform": "state", "entity_id": gate}],
            "action": _hamcp542_actions(codes),
        }),
        record(
            "automation.synthetic_keypad_blueprint",
            {"id": "synthetic_keypad_blueprint", "alias": "Synthetic keypad blueprint",
             "use_blueprint": {"path": "synthetic/keypad_disarm.yaml", "input": blueprint_input}},
            loaded={
                "id": "synthetic_keypad_blueprint", "alias": "Synthetic keypad blueprint",
                "trigger": [{"platform": "state", "entity_id": gate}],
                "action": [{"service": "alarm_control_panel.alarm_disarm",
                            "target": {"entity_id": "alarm_control_panel.synthetic_panel"},
                            "data": {"code": codes["blueprint input code"]}}],
            },
            blueprint="synthetic/keypad_disarm.yaml",
        ),
    ])
    base = ha.services
    ha.services = lambda: _hamcp542_services(base)
    return ha


def _keypad_entry_home(gate, value):
    """One password-selector field whose name is no credential name.

    Only the provider's schema rule withholds ``keypad_entry``.
    """

    ha = SyntheticHomeAssistant([
        record("automation.synthetic_keypad_entry", None, automation_id="synthetic_keypad_entry",
               loaded={
                   "id": "synthetic_keypad_entry", "alias": "Synthetic keypad entry",
                   "trigger": [{"platform": "state", "entity_id": gate}],
                   "action": [{"service": "synthetic_keypad.enter",
                               "data": {"keypad_entry": value}}],
               }),
    ])
    base = ha.services
    ha.services = lambda: _hamcp542_services(base)
    return ha


# Every emitted profile digest that covers the action data.
_VALUE_BINDINGS = (
    "effect_data_fingerprint", "effect_structure_fingerprint",
    "effect_projection_fingerprint", "evidence_fingerprint",
)


def _code_hits(text, codes=None):
    """Names of codes whose value appears in ``text`` (as a number or string)."""

    return sorted(
        name for name, value in (codes or _CODES).items()
        if (str(value) if not isinstance(value, str) else value) in text
    )


def _unkeyed_digests(value):
    """Every plain digest an unkeyed builder could have emitted for a code.

    Bare and JSON forms (risk.py hashed the JSON value) and ``path=value``
    forms for each data path in the matrix (provider.py hashed the token).
    """

    forms = {str(value), json.dumps(value), json.dumps(str(value))}
    paths = [
        f"action[{step}].data.{key}"
        for step in range(16)
        for key in ("code", "pin", "passcode", "keypad_entry", "data", "variables", "flash")
    ]
    forms |= {f"{path}={form}" for path in paths for form in tuple(forms)}
    return {hashlib.sha256(form.encode("utf-8")).hexdigest() for form in forms}


def _brute_force(text):
    """Recover any six-digit code from plain sha256 digests in ``text``."""

    found = set(re.findall(r"[0-9a-f]{64}", text))
    recovered = []
    if not found:
        return recovered
    sha256 = hashlib.sha256
    for number in range(1_000_000):
        plain = b"%06d" % number
        for form in (plain, b'"' + plain + b'"'):
            if sha256(form).hexdigest() in found:
                recovered.append(plain.decode("ascii"))
    return recovered


def _unkeyed_hasher(data=b"", **_):
    """A mutant ``_effect_hasher``: plain sha256 for every key choice."""

    return hashlib.sha256(data)


def _keyed_brute_force(text, key):
    """Recover a six-digit code from HMAC(key, ...) digests in ``text``.

    The attacker of Codex #546 R1-H1: every connector client knows the
    installation secret, so it can derive ``effect_digest_key``.
    """

    found = set(re.findall(r"[0-9a-f]{64}", text))
    recovered = []
    if not found:
        return recovered
    for number in range(1_000_000):
        plain = b"%06d" % number
        for form in (plain, b'"' + plain + b'"', str(number).encode("ascii")):
            if hmac.new(key, form, hashlib.sha256).hexdigest() in found:
                recovered.append(plain.decode("ascii"))
    return recovered


class NumericEffectSecretTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-542: a credential in action data is withheld on every surface.

    A value is withheld when its registered field has a free-form selector
    (Core 2026.11.3 ``text``/``template``/``object``, or none), or when its
    exact leaf name is a credential name.  A withheld value is shown as the
    fixed marker and is bound only by a keyed digest.
    """

    async def scan(self, gate="input_boolean.synthetic_gate", *, secret=SECRET, codes=None):
        ha = _hamcp542_home(gate, codes)
        provider = DirectHaDependencyProvider(
            _AnalysisRest(ha), _AnalysisWebSocket(ha), secret=secret,
        )
        with patch(BLUEPRINT_READER, side_effect=_hamcp542_blueprint):
            scan = await provider.scan()
        return {
            profile.source_entity_id: profile for profile in scan.automation_action_profiles
        }

    async def test_provider_scan_withholds_codes_and_keeps_ordinary_numbers(self):
        profiles = await self.scan()
        profile = profiles["automation.synthetic_keypad"]
        tokens = set(profile.effect_data)
        self.assertEqual(_code_hits(_serialized([asdict(p) for p in profiles.values()])), [])
        for token in _WITHHELD_TOKENS:
            self.assertIn(token, tokens)
        for token in _VERBATIM_TOKENS:
            self.assertIn(token, tokens)
        # A nested value (notify data.data.pin, script variables.code) is not
        # a top-level data key, so it is folded into a step-level keyed digest.
        for prefix in ("action[6].data.data", "action[13].data.variables"):
            self.assertFalse(any(t.startswith(prefix) for t in tokens), tokens)
        self.assertTrue(any(t.startswith("action[15].data.flash=sha256:") for t in tokens), tokens)
        for token in tokens:
            self.assertNotIn("withheld:", token)
        blueprint = profiles["automation.synthetic_keypad_blueprint"]
        self.assertIn("action[0].data.code=withheld", blueprint.effect_data)

    async def test_changed_code_changes_bindings_under_one_key_only(self):
        changed = {**_CODES, "alarm code int": 860399, "notify nested pin": 931899}
        first = (await self.scan())["automation.synthetic_keypad"]
        same = (await self.scan())["automation.synthetic_keypad"]
        other = (await self.scan(codes=changed))["automation.synthetic_keypad"]
        rekeyed = (await self.scan(secret=SECRET + "-other-installation"))[
            "automation.synthetic_keypad"
        ]
        # The displayed tokens do not move with the code ...
        self.assertEqual(first.effect_data, other.effect_data)
        # ... but the bindings do, and they are stable under one key.
        for field in ("effect_data_fingerprint", "evidence_fingerprint"):
            with self.subTest(field=field):
                self.assertEqual(getattr(first, field), getattr(same, field))
                self.assertNotEqual(getattr(first, field), getattr(other, field))
                self.assertNotEqual(getattr(first, field), getattr(rekeyed, field))

    async def test_no_code_is_recoverable_from_any_digest(self):
        profiles = await self.scan()
        text = _serialized([asdict(p) for p in profiles.values()])
        for name, value in _CODES.items():
            with self.subTest(code=name):
                for digest in _unkeyed_digests(value):
                    self.assertNotIn(digest, text)
        self.assertEqual(_brute_force(text), [])

    def test_credential_leaf_names_are_exact(self):
        from ha_mcp_engineering.governance import risk

        for path in (
            "action[0].data.code", "action[0].data.pin", "action[0].data.passcode",
            "action[0].data.password", "action[0].data.access_code",
            "action[0].data.data.pin", "action[0].data.variables.code",
            "action[0].data.codes[0].code", "action[0].data.CODE",
        ):
            with self.subTest(path=path):
                self.assertTrue(risk._effect_path_is_sensitive(path))
        for path in (
            "action[0].data.zip_code", "action[0].data.country_code",
            "action[0].data.color_code", "action[0].data.brightness",
            "action[0].data.value", "action[0].data.codec",
        ):
            with self.subTest(path=path):
                self.assertFalse(risk._effect_path_is_sensitive(path))

    def test_provider_and_risk_share_the_withheld_marker(self):
        from ha_mcp_engineering.dependency import provider as module
        from ha_mcp_engineering.governance import risk

        self.assertEqual(module._EFFECT_WITHHELD, risk.HELPER_EFFECT_WITHHELD)
        self.assertEqual(module._EFFECT_WITHHELD, "withheld")

    def test_selector_decides_withholding(self):
        from ha_mcp_engineering.dependency.provider import _effect_field_withheld

        for selector, withheld in (
            ({"number": {}}, False), ({"boolean": None}, False), ({"state": {}}, False),
            ({"select": {"options": ["a"]}}, False), ({"color_rgb": None}, False),
            ({"text": None}, True), ({"text": {"type": "password"}}, True),
            ({"template": None}, True), ({"object": None}, True),
            ({}, True), (None, True), ("text", True),
        ):
            with self.subTest(selector=selector):
                field = {"selector": selector} if selector is not None else {}
                self.assertIs(_effect_field_withheld("synthetic.service", "x", field), withheld)
        # Core 2026.11.3 number.set_value value is a text selector holding a
        # number; it is the one reviewed exception.
        self.assertFalse(_effect_field_withheld("number.set_value", "value", {"selector": {"text": None}}))
        self.assertTrue(_effect_field_withheld("number.set_value", "code", {"selector": {"text": None}}))

    async def test_mutant_without_the_credential_leaf_rule_leaks(self):
        from ha_mcp_engineering.governance import risk

        with patch.object(risk, "_HELPER_EFFECT_CREDENTIAL_FIELDS", frozenset()):
            profiles = await self.scan()
        text = _serialized([asdict(p) for p in profiles.values()])
        self.assertIn("keypad code (number selector)", _code_hits(text))

    async def test_mutant_without_the_selector_rule_leaks(self):
        from ha_mcp_engineering.dependency import provider as module

        with patch.object(module, "_effect_field_withheld", return_value=False):
            profiles = await self.scan()
        text = _serialized([asdict(p) for p in profiles.values()])
        self.assertIn("keypad entry (password selector)", _code_hits(text))

    async def test_mutant_with_an_unkeyed_digest_is_brute_forced(self):
        from ha_mcp_engineering.governance import risk

        with patch.object(risk, "_effect_hasher", side_effect=_unkeyed_hasher):
            profiles = await self.scan()
        text = _serialized([asdict(p) for p in profiles.values()])
        self.assertIn("552911", _brute_force(text))

    def test_a_caller_who_knows_the_secret_cannot_recover_a_code(self):
        """Codex #546 R1-H1: the attacker knows access_secret.

        The digest of a withheld value, and the fingerprint over its binding,
        are keyed with the per-process key, so neither is rebuilt from the
        caller-known key; the mutant that keys them with it is recovered.
        """

        from ha_mcp_engineering.governance import risk

        config = {"action": [{
            "service": "alarm_control_panel.alarm_disarm",
            "target": {"entity_id": "alarm_control_panel.synthetic_panel"},
            "data": {"code": 860317},
        }]}
        key = effect_digest_key(SECRET)

        def recover(profile):
            self.assertEqual(profile["effect_data"], ["action[0].data.code=withheld"])
            wanted = profile["effect_data_fingerprint"]
            for number in range(1_000_000):
                inner = hmac.new(key, str(number).encode("ascii"), hashlib.sha256).hexdigest()
                material = '["action[0].data.code=withheld:' + inner + '"]'
                if hmac.new(key, material.encode("ascii"), hashlib.sha256).hexdigest() == wanted:
                    return number
            return None

        profile = risk.automation_action_consequence_profile(config, digest_key=key)
        text = _serialized(profile)
        self.assertIsNone(recover(profile))
        self.assertEqual(_keyed_brute_force(text, key), [])
        for secret in (risk.withheld_effect_digest_key().hex(), key.hex()):
            self.assertNotIn(secret, text)
        with patch.object(risk, "_PROCESS_WITHHELD_DIGEST_KEY", key):
            mutant = risk.automation_action_consequence_profile(config, digest_key=key)
        self.assertEqual(recover(mutant), 860317)

    async def test_no_code_is_recoverable_with_the_caller_known_key(self):
        from ha_mcp_engineering.governance import risk

        key = effect_digest_key(SECRET)
        text = _serialized([asdict(p) for p in (await self.scan()).values()])
        # The select-field value is not withheld (decision B keeps its
        # digest under the installation key; the PR's class sweep lists it).
        # Every withheld code stays unrecoverable; the fingerprint mutant is
        # test_a_caller_who_knows_the_secret_cannot_recover_a_code.
        self.assertEqual(set(_keyed_brute_force(text, key)), {"552911"})
        self.assertNotIn(risk.withheld_effect_digest_key().hex(), text)

    def test_a_schema_withheld_field_is_bound_under_the_withheld_key(self):
        """A field withheld only by its selector, not by its name.

        The attacker knows access_secret and so can rebuild, in its own
        process, any digest under the caller-known key: a match confirms a
        guessed code.  With the predicate nothing the profile emits is
        rebuilt; without it (the mutant) every binding is.
        """

        from ha_mcp_engineering.governance import risk

        config = {"action": [{"service": "synthetic_keypad.enter",
                              "data": {"keypad_entry": "418813"}}]}
        key = effect_digest_key(SECRET)

        def withheld(service, field):
            return (service, field) == ("synthetic_keypad.enter", "keypad_entry")

        def profile(predicate, attacker=False):
            # The attacker's process has its own withheld key.
            withheld_key = secrets.token_bytes(32) if attacker else risk.withheld_effect_digest_key()
            with patch.object(risk, "_PROCESS_WITHHELD_DIGEST_KEY", withheld_key):
                return risk.automation_action_consequence_profile(
                    config, digest_key=key, withheld_field=predicate
                )

        fixed = profile(withheld)
        self.assertEqual(fixed["effect_data"], ["action[0].data.keypad_entry=withheld"])
        self.assertEqual(_keyed_brute_force(_serialized(fixed), key), [])
        self.assertEqual(fixed, profile(withheld))
        rebuilt = profile(withheld, attacker=True)
        for field in _VALUE_BINDINGS:
            with self.subTest(field=field):
                self.assertNotEqual(fixed[field], rebuilt[field])
        mutant = profile(None)
        self.assertEqual(set(_keyed_brute_force(_serialized(mutant), key)), {"418813"})
        self.assertEqual(mutant, profile(None, attacker=True))

    def test_a_coverage_overflow_at_a_schema_withheld_field_is_withheld(self):
        """The effect-node limit stops on the withheld field itself.

        The coverage overflow digest covers that field's value, so it is keyed
        with the withheld key; the mutant (no predicate) is rebuilt by the
        attacker.
        """

        from ha_mcp_engineering.governance import risk

        config = {"action": [{"service": "synthetic_keypad.enter",
                              "data": {"a_level": 1, "keypad_entry": "418813"}}]}
        key = effect_digest_key(SECRET)

        def withheld(service, field):
            return (service, field) == ("synthetic_keypad.enter", "keypad_entry")

        def overflow(predicate, attacker=False):
            withheld_key = secrets.token_bytes(32) if attacker else risk.withheld_effect_digest_key()
            # The data dict and a_level fill the limit; keypad_entry overflows.
            with patch.object(risk, "_PROCESS_WITHHELD_DIGEST_KEY", withheld_key), \
                    patch.object(risk, "_HELPER_EFFECT_ANALYSIS_NODE_LIMIT", 2):
                reset_key = risk._EFFECT_DIGEST_KEY.set(key)
                reset_field = risk._EFFECT_WITHHELD_FIELD.set(predicate)
                try:
                    coverage = risk._bounded_action_analysis_coverage(config)
                finally:
                    risk._EFFECT_WITHHELD_FIELD.reset(reset_field)
                    risk._EFFECT_DIGEST_KEY.reset(reset_key)
            self.assertEqual(coverage["reason"], "effect_data_node_limit_exceeded")
            return coverage["overflow_fingerprint"]

        fixed = overflow(withheld)
        self.assertEqual(fixed, overflow(withheld))
        self.assertNotEqual(fixed, overflow(withheld, attacker=True))
        self.assertEqual(overflow(None), overflow(None, attacker=True))

    async def test_scan_binds_a_schema_withheld_field_under_the_withheld_key(self):
        from ha_mcp_engineering.dependency import provider as provider_module
        from ha_mcp_engineering.governance import risk

        async def scan(attacker=False):
            ha = _keypad_entry_home("input_boolean.synthetic_gate", "418813")
            provider = DirectHaDependencyProvider(
                _AnalysisRest(ha), _AnalysisWebSocket(ha), secret=SECRET,
            )
            withheld_key = secrets.token_bytes(32) if attacker else risk.withheld_effect_digest_key()
            with patch.object(risk, "_PROCESS_WITHHELD_DIGEST_KEY", withheld_key):
                result = await provider.scan()
            (profile,) = result.automation_action_profiles
            return profile

        fixed = await scan()
        self.assertEqual(list(fixed.effect_data), ["action[0].data.keypad_entry=withheld"])
        self.assertEqual(_keyed_brute_force(_serialized(asdict(fixed)), effect_digest_key(SECRET)), [])
        same, rebuilt = await scan(), await scan(attacker=True)
        for field in _VALUE_BINDINGS:
            with self.subTest(field=field):
                self.assertEqual(getattr(fixed, field), getattr(same, field))
                self.assertNotEqual(getattr(fixed, field), getattr(rebuilt, field))
        # Mutant: the provider passes no schema predicate.
        with patch.object(provider_module, "_has_schema_withheld_field", return_value=False):
            mutant, mutant_rebuilt = await scan(), await scan(attacker=True)
        self.assertEqual(mutant.effect_data, fixed.effect_data)
        for field in _VALUE_BINDINGS:
            with self.subTest(mutant=field):
                self.assertEqual(getattr(mutant, field), getattr(mutant_rebuilt, field))

    def test_an_unknown_schema_withholds_by_default(self):
        from ha_mcp_engineering.dependency import provider as provider_module

        unavailable = provider_module._schema_withheld_field(None)
        self.assertTrue(unavailable("light.turn_on", "brightness"))
        self.assertFalse(unavailable("light.turn_on", "entity_id"))
        registry = SimpleNamespace(
            services={"light.turn_on": frozenset({"brightness"}),
                      "synthetic_keypad.enter": frozenset({"keypad_entry"})},
            withheld_fields={"synthetic_keypad.enter": frozenset({"keypad_entry"})},
        )
        known = provider_module._schema_withheld_field(registry)
        self.assertTrue(known("synthetic_keypad.enter", "keypad_entry"))
        # No withheld-field facts for the service: fail closed.
        self.assertTrue(known("light.turn_on", "brightness"))
        self.assertTrue(known("{{ service }}", "brightness"))
        self.assertTrue(known(["light.turn_on"], "brightness"))
        registry.withheld_fields["light.turn_on"] = frozenset()
        self.assertFalse(known("light.turn_on", "brightness"))
        self.assertFalse(known("light.turn_on", "not_in_schema"))

    def test_fingerprint_does_not_reveal_a_withheld_code(self):
        """A caller who sees the withheld tokens cannot rebuild the fingerprint.

        ``effect_data_fingerprint`` covers ``path=withheld:<digest of value>``.
        Unkeyed, every six-digit candidate rebuilds it in seconds; keyed, none
        does.
        """

        from ha_mcp_engineering.governance import risk

        config = {"action": [{
            "service": "alarm_control_panel.alarm_disarm",
            "target": {"entity_id": "alarm_control_panel.synthetic_panel"},
            "data": {"code": 860317},
        }]}

        def recover(profile):
            self.assertEqual(profile["effect_data"], ["action[0].data.code=withheld"])
            wanted = profile["effect_data_fingerprint"]
            sha256 = hashlib.sha256
            for number in range(1_000_000):
                inner = sha256(str(number).encode("ascii")).hexdigest()
                material = '["action[0].data.code=withheld:' + inner + '"]'
                if sha256(material.encode("ascii")).hexdigest() == wanted:
                    return number
            return None

        key = effect_digest_key(SECRET)
        self.assertIsNone(recover(risk.automation_action_consequence_profile(config, digest_key=key)))
        with patch.object(risk, "_effect_hasher", side_effect=_unkeyed_hasher):
            unkeyed = risk.automation_action_consequence_profile(config, digest_key=key)
        self.assertEqual(recover(unkeyed), 860317)

    async def test_mutant_with_an_unkeyed_provider_digest_is_detected(self):
        from ha_mcp_engineering.dependency import provider as module

        # The panel is not in the registry, so its target id is digested.
        plain = hashlib.sha256(b"alarm_control_panel.synthetic_panel").hexdigest()
        keyed = _serialized([asdict(p) for p in (await self.scan()).values()])
        self.assertNotIn(plain, keyed)

        def unkeyed(value, key):
            return "redacted_sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

        with patch.object(module, "_effect_digest", side_effect=unkeyed):
            profiles = await self.scan()
        self.assertIn(plain, _serialized([asdict(p) for p in profiles.values()]))

    async def test_every_helper_binding_withholds_codes(self):
        from ha_mcp_engineering.governance.helper_dependency import (
            build_helper_dependency_risk_binding,
        )
        from ha_mcp_engineering.governance.input_boolean_dependency import (
            build_input_boolean_dependency_binding,
        )
        from ha_mcp_engineering.governance.input_select_dependency import (
            build_input_select_dependency_binding,
        )

        emitted = []
        for gate, build in (
            ("input_boolean.synthetic_gate", build_input_boolean_dependency_binding),
            ("input_select.synthetic_mode", build_input_select_dependency_binding),
            ("input_number.synthetic_level", lambda snapshot, **values: (
                build_helper_dependency_risk_binding(snapshot, target_scoped=True, **values)
            )),
        ):
            with self.subTest(gate=gate):
                ha = _hamcp542_home(gate)
                index = DependencyIndex(DirectHaDependencyProvider(
                    _AnalysisRest(ha), _AnalysisWebSocket(ha), secret=SECRET,
                ))
                with patch(BLUEPRINT_READER, side_effect=_hamcp542_blueprint):
                    snapshot, _, _ = await index.get(refresh=True)
                    assessed = await HelperDependencyRiskService(index).assess(gate, refresh=False)
                binding = build(
                    snapshot, entity_id=gate, index_metadata=index.evidence_metadata(snapshot),
                )
                for label, value in (("binding", binding), ("assess", assessed)):
                    text = _serialized(value)
                    emitted.append(text)
                    self.assertEqual(_code_hits(text), [], label)
                    self.assertIn("action[0].data.code=withheld", text, label)
                    self.assertIn("action[10].data.brightness=10", text, label)
        self.assertEqual(_brute_force("\n".join(emitted)), [])

    async def test_helper_plan_surfaces_withhold_codes(self):
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.governance.service import ChangeGovernanceService
        from ha_mcp_engineering.governance.storage import ChangePlanRepository
        from tests.test_beta37_exact_helper_state import (
            Clock, FakeHelperStateGateway, UnusedLegacyGateway,
        )

        gateway = FakeHelperStateGateway()
        ha = _hamcp542_home(gateway.entity_id)
        index = DependencyIndex(DirectHaDependencyProvider(
            _AnalysisRest(ha), _AnalysisWebSocket(ha), secret=SECRET,
        ))
        outputs = {}
        with tempfile.TemporaryDirectory(prefix="hamcp542-plan-") as directory, \
                patch(BLUEPRINT_READER, side_effect=_hamcp542_blueprint):
            root = Path(directory)
            service = ChangeGovernanceService(
                ChangePlanRepository(root / "plans"),
                UnusedLegacyGateway(),
                AuditLogger(str(root / "audit.jsonl"), "synthetic-audit-key"),
                now=Clock(),
                helper_state_gateway=gateway,
                helper_dependency_risk_reader=HelperDependencyRiskService(index).assess,
                plan_observability_cursor_key=b"synthetic-plan-key" * 2,
            )
            created = await service.create_helper_state_plan(
                entity_id=gateway.entity_id, desired_state="on",
            )
            outputs["create_helper_state_plan"] = created
            plan = created["plan"]
            outputs["get_plan"] = service.get_plan(plan["plan_id"])
            for section in ("summary", "obligation_evidence", "downstream_profiles"):
                outputs[f"get_change_plan {section}"] = service.get_plan_observability(
                    plan["plan_id"], detail_section=section, cursor="", page_size=100,
                    response_limit=60_000,
                )
            try:
                outputs["approve"] = service.approve(plan["plan_id"], plan["plan_hash"])
            except Exception as error:  # noqa: BLE001 - the refusal text is a surface too
                outputs["approve refusal"] = f"{type(error).__name__}: {error} {getattr(error, 'details', '')}"
            files = {
                str(path.relative_to(root)): path.read_bytes()
                for path in sorted(root.rglob("*")) if path.is_file()
            }
        self.assertEqual(gateway.dispatch_count, 0)
        self.assertTrue(any(name.endswith(".json") for name in files), files)
        self.assertIn("audit.jsonl", files)
        emitted = []
        for label, value in [*outputs.items(), *files.items()]:
            text = value.decode("utf-8") if isinstance(value, bytes) else _serialized(value)
            emitted.append(text)
            with self.subTest(surface=label):
                self.assertEqual(_code_hits(text), [])
                for name, code in _CODES.items():
                    for digest in _unkeyed_digests(code):
                        self.assertNotIn(digest, text, name)
        self.assertEqual(_brute_force("\n".join(emitted)), [])
        # Decision B: the per-process withheld key reaches no reply or file.
        withheld_key = withheld_effect_digest_key()
        for text in emitted:
            self.assertNotIn(withheld_key.hex(), text)
            self.assertNotIn(base64.b64encode(withheld_key).decode("ascii"), text)
        for name, raw in files.items():
            self.assertNotIn(withheld_key, raw, name)
        downstream = _serialized(outputs["get_change_plan downstream_profiles"])
        self.assertIn("action[0].data.code=withheld", downstream)
        self.assertIn("action[7].data.value=39177", downstream)
        self.assertIn("action[10].data.brightness=10", downstream)

    async def test_plan_bound_under_another_key_drifts(self):
        """A binding made before the upgrade (another key) cannot pass dispatch.

        The pre-dispatch check compares ``evidence_fingerprint``
        (``f3/operational_strategies.py``: ``dependency_risk_drift``), so a
        changed digest key is a drift, never a silent accept.
        """

        bindings = {}
        for name, secret in (("before", SECRET), ("after", SECRET + "-rekeyed")):
            ha = _hamcp542_home("input_boolean.synthetic_gate")
            index = DependencyIndex(DirectHaDependencyProvider(
                _AnalysisRest(ha), _AnalysisWebSocket(ha), secret=secret,
            ))
            with patch(BLUEPRINT_READER, side_effect=_hamcp542_blueprint):
                assessed = await HelperDependencyRiskService(index).assess(
                    "input_boolean.synthetic_gate", refresh=True,
                )
            bindings[name] = assessed["binding"]
        self.assertNotEqual(
            bindings["before"]["evidence_fingerprint"], bindings["after"]["evidence_fingerprint"],
        )



_PRE_FIX_MODEL = "automation-action-effect-v2"
_PRE_FIX_CODE = "418207"
_PRE_FIX_DIGEST = hashlib.sha256(json.dumps(_PRE_FIX_CODE).encode()).hexdigest()


def _effect_profiles(value):
    stack, found = [value], []
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "effect_projection_model" in node:
                found.append(node)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found


def _forge_pre_fix(record, status, plan_id=None):
    """A synthetic record as the pre-fix (v2) projection stored it."""

    value = deepcopy(record)
    value["plan_id"] = plan_id or secrets.token_hex(16)
    value["status"] = status
    profiles = [p for p in _effect_profiles(value) if p["effect_data"]]
    assert profiles, "the source plan has no effect tokens"
    for profile in profiles:
        profile["effect_projection_model"] = _PRE_FIX_MODEL
        profile["effect_data"] = [
            *profile["effect_data"],
            f"action[0].data.code={_PRE_FIX_CODE}",
            f"action[1].data.code=sha256:{_PRE_FIX_DIGEST}",
        ]
    return value


def _store_bytes(root):
    return b"\n".join(
        path.read_bytes() for path in sorted(Path(root).rglob("*")) if path.is_file()
    )


class PreFixEffectRecordUpgradeTests(unittest.IsolatedAsyncioTestCase):
    """HAMCP-542 item 5: the startup pass over records written before the fix.

    A rewrite would change the plan hash (it covers the stored baseline), so
    each affected record is removed, counted, and must be planned again.
    """

    async def asyncSetUp(self):
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.governance.service import ChangeGovernanceService
        from ha_mcp_engineering.governance.storage import ChangePlanRepository
        from tests.test_beta37_exact_helper_state import (
            Clock, FakeHelperStateGateway, UnusedLegacyGateway,
        )

        self.temporary = tempfile.TemporaryDirectory(prefix="hamcp542-upgrade-")
        self.root = Path(self.temporary.name)
        self.plans = self.root / "plans"
        gateway = FakeHelperStateGateway()
        ha = _hamcp542_home(gateway.entity_id)
        index = DependencyIndex(DirectHaDependencyProvider(
            _AnalysisRest(ha), _AnalysisWebSocket(ha), secret=SECRET,
        ))
        with patch(BLUEPRINT_READER, side_effect=_hamcp542_blueprint):
            service = ChangeGovernanceService(
                ChangePlanRepository(self.plans),
                UnusedLegacyGateway(),
                AuditLogger(str(self.root / "audit.jsonl"), "synthetic-audit-key"),
                now=Clock(),
                helper_state_gateway=gateway,
                helper_dependency_risk_reader=HelperDependencyRiskService(index).assess,
                plan_observability_cursor_key=b"synthetic-plan-key" * 2,
            )
            created = await service.create_helper_state_plan(
                entity_id=gateway.entity_id, desired_state="on",
            )
        self.current = next(self.plans.rglob(f"{created['plan']['plan_id']}.json"))
        self.current_bytes = self.current.read_bytes()
        self.record = json.loads(self.current_bytes)
        self.assertTrue(any(p["effect_data"] for p in _effect_profiles(self.record)))
        self.assertEqual(
            {p["effect_projection_model"] for p in _effect_profiles(self.record)},
            {"automation-action-effect-v3"},
        )
        self.namespace = self.current.parent
        self.assertNotIn(_PRE_FIX_CODE.encode(), _store_bytes(self.root))

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def write_pre_fix(self, status, *, quarantined=False):
        value = _forge_pre_fix(self.record, status)
        if quarantined:
            path = self.namespace / "quarantine" / f"{value['plan_id']}.1700000000.corrupt"
            if not path.parent.is_dir():
                path = self.plans / "quarantine" / path.name
        else:
            path = self.namespace / f"{value['plan_id']}.json"
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        return value["plan_id"], path

    def repository(self):
        from ha_mcp_engineering.governance.storage import ChangePlanRepository

        return ChangePlanRepository(self.plans)

    @staticmethod
    def changed(counts):
        return {key: value for key, value in counts.items() if key != "scanned" and value}

    def test_pending_approved_and_terminal_records_are_removed(self):
        forged = {
            status: self.write_pre_fix(status)
            for status in ("awaiting_approval", "approved", "expired", "applied")
        }
        _, held = self.write_pre_fix("awaiting_approval", quarantined=True)
        self.assertIn(_PRE_FIX_CODE.encode(), _store_bytes(self.root))
        repository = self.repository()
        for plan_id, _ in forged.values():
            self.assertIsNotNone(repository.get(plan_id))

        counts = repository.retire_pre_fix_effect_records()

        self.assertEqual(self.changed(counts), {"removed": 4, "quarantine_removed": 1})
        for plan_id, path in forged.values():
            self.assertFalse(path.exists())
            self.assertIsNone(repository.get(plan_id))
        self.assertFalse(held.exists())
        self.assertEqual(self.current.read_bytes(), self.current_bytes)
        self.assertIsNotNone(repository.get(self.record["plan_id"]))
        stored = _store_bytes(self.root)
        self.assertNotIn(_PRE_FIX_CODE.encode(), stored)
        self.assertNotIn(_PRE_FIX_DIGEST.encode(), stored)
        self.assertEqual(_code_hits(stored.decode("utf-8")), [])
        # Idempotent: a second run (and a restart) changes nothing.
        self.assertEqual(self.changed(repository.retire_pre_fix_effect_records()), {})
        self.assertEqual(
            self.changed(self.repository().retire_pre_fix_effect_records()), {}
        )
        self.assertEqual(stored, _store_bytes(self.root))

    def test_records_in_dispatch_or_recovery_are_deferred(self):
        deferred = [
            self.write_pre_fix(status)
            for status in ("applying", "verification_required", "rollback_pending")
        ]
        counts = self.repository().retire_pre_fix_effect_records()
        self.assertEqual(self.changed(counts), {"deferred": 3})
        for _, path in deferred:
            self.assertTrue(path.exists())

    def test_post_fix_and_profile_free_records_are_untouched(self):
        empty = deepcopy(self.record)
        empty["plan_id"] = secrets.token_hex(16)
        for profile in _effect_profiles(empty):
            profile["effect_projection_model"] = _PRE_FIX_MODEL
            profile["effect_data"] = []
            profile["effect_targets"] = []
        path = self.namespace / f"{empty['plan_id']}.json"
        path.write_text(json.dumps(empty, sort_keys=True), encoding="utf-8")
        before = _store_bytes(self.root)
        counts = self.repository().retire_pre_fix_effect_records()
        self.assertEqual(self.changed(counts), {})
        self.assertGreaterEqual(counts["scanned"], 2)
        self.assertEqual(before, _store_bytes(self.root))

    def test_committed_historical_stores_are_handled_by_shape(self):
        """Beta 6 contract-v2 records hold no effect data and stay byte-exact;
        the HAMCP-304 beta.3 helper plans that hold effect tokens are removed,
        and the one without them stays."""
        import shutil

        from ha_mcp_engineering.governance.storage import ChangePlanRepository

        fixtures = ROOT / "tests" / "fixtures"
        legacy = sorted(fixtures.glob("beta6_*contract_v2_[ab].json")) + sorted(
            fixtures.glob("beta6_legacy_*_[ab].json")
        )
        self.assertGreaterEqual(len(legacy), 4)
        with tempfile.TemporaryDirectory(prefix="hamcp542-legacy-") as scratch:
            plans = Path(scratch) / "plans"
            plans.mkdir()
            for source in legacy:
                record = json.loads(source.read_text(encoding="utf-8"))
                shutil.copyfile(source, plans / f"{record['plan_id']}.json")
            before = _store_bytes(plans)
            counts = ChangePlanRepository(plans).retire_pre_fix_effect_records()
            self.assertEqual(self.changed(counts), {})
            self.assertEqual(before, _store_bytes(plans))

        expected = {
            "expired_elevated_input_boolean": 1,
            "prohibited_input_boolean_acknowledged": 1,
            "prohibited_input_boolean_unacknowledged": 1,
            "prohibited_input_select_acknowledged": 0,
        }
        for name, removed in expected.items():
            with self.subTest(fixture=name), tempfile.TemporaryDirectory(
                prefix="hamcp542-304-"
            ) as scratch:
                plans = Path(scratch) / "plans"
                shutil.copytree(
                    fixtures / "hamcp304_beta3_prohibited_plans" / name / "plans", plans
                )
                counts = ChangePlanRepository(plans).retire_pre_fix_effect_records()
                self.assertEqual(
                    self.changed(counts), {"removed": removed} if removed else {}
                )
                remaining = sorted((plans / "operational-administration-v3").glob("*.json"))
                self.assertEqual(len(remaining), 1 - removed)

    def test_a_record_that_cannot_be_removed_is_quarantined(self):
        plan_id, path = self.write_pre_fix("awaiting_approval")
        repository = self.repository()
        original = Path.unlink

        def refuse(target, *args, **kwargs):
            if target == path:
                raise PermissionError("synthetic")
            return original(target, *args, **kwargs)

        with patch.object(Path, "unlink", refuse):
            counts = repository.retire_pre_fix_effect_records()
        self.assertEqual(self.changed(counts), {"quarantined": 1})
        self.assertFalse(path.exists())
        self.assertIsNone(repository.get(plan_id))
        # The quarantined copy is parseable and pre-fix: the next startup
        # removes it rather than restoring it.
        counts = self.repository().retire_pre_fix_effect_records()
        self.assertEqual(self.changed(counts), {"quarantine_removed": 1})
        self.assertNotIn(_PRE_FIX_CODE.encode(), _store_bytes(self.root))

    def test_a_record_that_can_be_neither_removed_nor_moved_is_failed(self):
        _, path = self.write_pre_fix("approved")
        repository = self.repository()
        original_unlink = Path.unlink

        def refuse(target, *args, **kwargs):
            if target == path:
                raise PermissionError("synthetic")
            return original_unlink(target, *args, **kwargs)

        with patch.object(Path, "unlink", refuse), patch(
            "ha_mcp_engineering.governance.storage.os.replace",
            side_effect=PermissionError("synthetic"),
        ):
            counts = repository.retire_pre_fix_effect_records()
        self.assertEqual(self.changed(counts), {"failed": 1})
        self.assertTrue(path.exists())

    def test_unparseable_record_with_effect_marker_is_quarantined(self):
        repository = self.repository()
        # Written after the index rebuild, so only the pass can meet it.
        path = self.namespace / f"{secrets.token_hex(16)}.json"
        path.write_bytes(b'{"effect_projection_model": "automation-action-effect-v2", ')
        counts = repository.retire_pre_fix_effect_records()
        self.assertEqual(self.changed(counts), {"quarantined": 1})
        self.assertFalse(path.exists())
        self.assertTrue(list(self.namespace.glob("quarantine/*.corrupt")))

    def test_every_live_record_is_reached_in_one_pass(self):
        """Codex #546 R1-H2: no prefix of unaffected, deferred or large
        records keeps a later pre-fix record from being removed, and the
        restoration bound applies only to quarantine copies."""

        from ha_mcp_engineering.governance import storage

        def write(value, plan_id):
            value["plan_id"] = plan_id
            path = self.namespace / f"{plan_id}.json"
            path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
            return path

        unaffected = [
            write(deepcopy(self.record), f"{index:032x}") for index in range(1, 41)
        ]
        deferred = write(
            _forge_pre_fix(self.record, "verification_required"), "a" * 32
        )
        large = _forge_pre_fix(self.record, "awaiting_approval")
        padding = "action[2].data.note=" + "x" * (4 * len(self.current_bytes))
        held = _forge_pre_fix(self.record, "expired")
        for value in (large, held):
            for profile in _effect_profiles(value):
                if profile["effect_data"]:
                    profile["effect_data"].append(padding)
        large = write(large, "e" * 32)
        last = write(_forge_pre_fix(self.record, "expired"), "f" * 32)
        held_path = self.namespace / "quarantine" / f"{'b' * 32}.1700000000.corrupt"
        held_path.parent.mkdir(exist_ok=True)
        held_path.write_text(json.dumps(held), encoding="utf-8")
        before = {path: path.read_bytes() for path in unaffected}
        with patch.object(
            storage, "MAX_QUARANTINE_RESTORE_BYTES", 2 * len(self.current_bytes)
        ):
            counts = self.repository().retire_pre_fix_effect_records()
            self.assertEqual(
                self.changed(counts),
                {"removed": 2, "deferred": 1, "quarantine_retained": 1},
            )
            self.assertFalse(large.exists())
            self.assertFalse(last.exists())
            self.assertTrue(deferred.exists())
            self.assertTrue(held_path.exists())
            self.assertEqual(
                before, {path: path.read_bytes() for path in unaffected}
            )
            again = self.repository().retire_pre_fix_effect_records()
        self.assertEqual(
            self.changed(again), {"deferred": 1, "quarantine_retained": 1}
        )
        # Within the restoration bound the quarantine copy is removed.
        final = self.repository().retire_pre_fix_effect_records()
        self.assertEqual(
            self.changed(final), {"deferred": 1, "quarantine_removed": 1}
        )
        self.assertFalse(held_path.exists())

    def test_mutant_without_the_model_guard_keeps_the_code_at_rest(self):
        from ha_mcp_engineering.governance.storage import ChangePlanRepository

        self.write_pre_fix("awaiting_approval")
        with patch.object(
            ChangePlanRepository, "_holds_pre_fix_effect_tokens",
            staticmethod(lambda value: False),
        ):
            counts = self.repository().retire_pre_fix_effect_records()
        self.assertEqual(self.changed(counts), {})
        self.assertIn(_PRE_FIX_CODE.encode(), _store_bytes(self.root))

    def test_mutant_without_the_deferral_removes_an_in_flight_record(self):
        from ha_mcp_engineering.governance import storage

        _, path = self.write_pre_fix("applying")
        with patch.object(storage, "EFFECT_UPGRADE_DEFERRED_STATUSES", frozenset()):
            self.repository().retire_pre_fix_effect_records()
        self.assertFalse(path.exists())

    def compose_runtime(self):
        from ha_mcp_engineering.audit import AuditLogger
        from ha_mcp_engineering.f3_runtime.runtime import F3RuntimeIntegration
        from ha_mcp_engineering.governance.service import ChangeGovernanceService
        from tests.test_beta37_exact_helper_state import (
            Clock, FakeHelperStateGateway, UnusedLegacyGateway,
        )
        from tests.test_f3_runtime_integration import _ExactFakeConfigurationGateway
        import test_dashboard_update_mvp as dashboard_suite

        helper = FakeHelperStateGateway()
        service = ChangeGovernanceService(
            self.repository(),
            UnusedLegacyGateway(),
            AuditLogger(str(self.root / "audit.jsonl"), "synthetic-audit-key"),
            now=Clock(),
            helper_state_gateway=helper,
            helper_dependency_risk_reader=None,
            plan_observability_cursor_key=b"synthetic-plan-key" * 2,
        )
        runtime = F3RuntimeIntegration(
            service=service,
            storage_root=str(self.plans),
            configuration_gateway=_ExactFakeConfigurationGateway(UnusedLegacyGateway()),
            backup_gateway=None,
            lifecycle_gateway=None,
            helper_state_gateway=helper,
            provider_identity_reader=dashboard_suite._unrelated_lifecycle_provider_identity,
            retention_days=90,
        )
        service.f3_runtime = runtime
        return service, runtime

    def upgrade_audit_records(self):
        path = self.root / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        return [
            record for record in map(json.loads, lines)
            if record.get("event") == "governance_effect_record_upgrade"
        ]

    async def test_startup_writes_one_count_only_audit_record(self):
        forged = [self.write_pre_fix(s)[1] for s in ("awaiting_approval", "approved", "expired")]
        _, runtime = self.compose_runtime()
        await runtime.recover_once("startup")
        for path in forged:
            self.assertFalse(path.exists())
        records = self.upgrade_audit_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        counts = {k: v for k, v in record.items() if k.startswith("records_")}
        self.assertEqual(counts["records_removed"], 3)
        self.assertEqual(
            {k for k in record if not k.startswith("records_")} - {"timestamp", "server_version"},
            {"event", "access", "operation_class"},
        )
        self.assertTrue(all(isinstance(v, int) for v in counts.values()))
        text = json.dumps(record)
        for path in forged:
            self.assertNotIn(path.stem, text)
        self.assertNotIn(_PRE_FIX_CODE, text)
        self.assertNotIn("effect_record_upgrade", runtime.readiness_state()["faults"])
        # A periodic pass does not run it; a restart finds nothing to do.
        late = self.write_pre_fix("approved")[1]
        await runtime.recover_once("periodic")
        self.assertTrue(late.exists())
        late.unlink()
        _, restarted = self.compose_runtime()
        await restarted.recover_once("startup")
        self.assertEqual(len(self.upgrade_audit_records()), 1)
        self.assertNotIn(_PRE_FIX_CODE.encode(), _store_bytes(self.root))

    async def test_a_failed_pass_faults_readiness(self):
        from ha_mcp_engineering.governance.storage import ChangePlanStorageError

        service, runtime = self.compose_runtime()
        with patch.object(
            service.repository, "retire_pre_fix_effect_records",
            side_effect=ChangePlanStorageError("synthetic"),
        ):
            await runtime.recover_once("startup")
        state = runtime.readiness_state()
        self.assertIn("effect_record_upgrade", state["faults"])
        self.assertFalse(state["execution_ready"])


if __name__ == "__main__":
    unittest.main()


from tests.hamcp285_fixtures import (  # noqa: E402
    ENTITY as _HAMCP285_ENTITY,
    InputBooleanServiceTestCase as _InputBooleanServiceTestCase,
    consumer as _consumer,
)


class WithheldBindingRestartTests(_InputBooleanServiceTestCase):
    """HAMCP-542 decision B: the withheld key does not survive a restart.

    A binding that covers a withheld value is keyed with a per-process key,
    so after a restart (simulated by a new key) its plan is refused before
    dispatch with ``dependency_risk_drift`` and must be replanned.  A plan
    with no withheld value is unaffected.
    """

    # Core 2026.11.3 light.turn_on brightness, and a synthetic password field.
    SERVICES = [
        {"domain": "light", "services": {"turn_on": {"fields": {
            "brightness": {"selector": {"number": {"min": 0, "max": 255}}},
        }}}},
        {"domain": "synthetic_keypad", "services": {"enter": {"fields": {
            "keypad_entry": {"selector": {"text": {"type": "password"}}},
        }}}},
    ]

    def install(self, *, services=True, **install):
        super().install(**install)
        inner = self.rest.request

        async def request(method, path, **kwargs):
            if services and method == "GET" and path == "/services":
                return deepcopy(self.SERVICES)
            return await inner(method, path, **kwargs)

        self.rest.request = request

    def gated(self, data, service="light.turn_on"):
        return {"gated": _consumer(
            "Synthetic gated", triggers=[{"trigger": "state", "entity_id": _HAMCP285_ENTITY}],
            actions=[{"action": service,
                      "target": {"entity_id": "light.synthetic_porch"}, "data": data}],
        )}

    async def restarted_apply(self, data, service="light.turn_on", services=True):
        plan = (await self.planned(
            "on", extra=self.gated(data, service), noise=False, services=services,
        ))["plan"]
        await self.grant(plan)
        from ha_mcp_engineering.governance import risk

        with patch.object(risk, "_PROCESS_WITHHELD_DIGEST_KEY", secrets.token_bytes(32)):
            return plan, await self.apply(plan)

    async def test_a_withheld_binding_must_be_replanned_after_a_restart(self):
        plan, result = await self.restarted_apply({"code": 418813})
        self.assertNotIn("418813", _serialized(self.stored_binding(plan)))
        self.assertEqual(result["task_state"], "failed_pre_dispatch", result)
        self.assertFalse(result["provider_dispatch_occurred"])
        self.assertIn("dependency_risk_drift", self.child_codes(result["task_id"]))
        self.assertEqual(self.helper.dispatch_count, 0)
        from ha_mcp_engineering.f3_runtime import pre_dispatch_reasons as reasons

        check = reasons.REASON_CHECKS["dependency_risk_drift"]
        self.assertEqual(check, "target_state")
        self.assertIn("fresh plan", reasons._CHECK_DETAILS[check])

    async def test_a_binding_without_a_withheld_value_survives_a_restart(self):
        _plan, result = await self.restarted_apply({"brightness": 10})
        self.assertNotIn("dependency_risk_drift", self.child_codes(result["task_id"]), result)
        self.assertTrue(result["provider_dispatch_occurred"], result)
        self.assertEqual(self.helper.dispatch_count, 1)

    async def test_a_schema_withheld_binding_must_be_replanned(self):
        plan, result = await self.restarted_apply(
            {"keypad_entry": "418813"}, service="synthetic_keypad.enter"
        )
        self.assertNotIn("418813", _serialized(self.stored_binding(plan)))
        self.assertIn("dependency_risk_drift", self.child_codes(result["task_id"]))
        self.assertEqual(self.helper.dispatch_count, 0)

    async def test_an_unknown_schema_binding_must_be_replanned(self):
        # No service registry: the schema cannot show a field is not free-form.
        _plan, result = await self.restarted_apply({"brightness": 10}, services=False)
        self.assertIn("dependency_risk_drift", self.child_codes(result["task_id"]))
        self.assertEqual(self.helper.dispatch_count, 0)
