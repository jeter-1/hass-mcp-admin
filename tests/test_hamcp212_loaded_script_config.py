"""HAMCP-212: read script configuration from Home Assistant's loaded config.

Owner decisions D3/D4 (HAMCP-171, HAMCP-209) extended to scripts: read the
loaded script configuration over the existing single WebSocket batch
(``script/config`` plus ``search/related``) instead of one delegated ha-mcp
read per script, and never read a configuration file. These tests prove:

* parity: the loaded source yields script diagnostics identical to the
  delegated stored path for plain, templated, dynamic, calling, blueprint,
  nested-blueprint, unavailable and categorized scripts;
* the request count is bounded (one connection, no delegated read for a
  plain loaded script, fixed read-only command shapes);
* every stored fallback is counted with its reason, never silently;
* a failed loaded read is partial (``loaded_config_unavailable``), never
  reported as complete;
* the pending-write journal keeps a governed script write that Core has not
  reloaded yet from being reported from stale loaded configuration;
* health's ``last_build_profile`` reports the script source counts;
* no analysis output or saved plan carries a secret-shaped value resolved
  into loaded script configuration (a package script's ``!secret``).

Every value is synthetic. No live dependency is used.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(ROOT))

from ha_mcp_engineering.clients.websocket import (  # noqa: E402
    BATCH_WINDOW,
    MAX_BATCH_COMMANDS,
    batch_command_allowed,
)
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    MAX_PENDING_SCRIPT_WRITES,
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.dependency.runtime import DependencyAnalysisRuntime  # noqa: E402
from ha_mcp_engineering.dependency.script_sources import (  # noqa: E402
    LOADED_CONFIG_UNAVAILABLE,
    PENDING_WRITE_UNRECONCILED,
    SCRIPT_LOADED_FALLBACK_REASONS,
)
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
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
from ha_mcp_engineering.errors import HomeAssistantUnavailableError  # noqa: E402

from tests.hamcp209_fixtures import (  # noqa: E402
    generated_automations,
    read_blueprint,
    record,
    representative_automations,
)
from tests.hamcp212_fixtures import (  # noqa: E402
    SyntheticScriptHome,
    SyntheticScriptReader,
    SyntheticScriptWebSocket,
    generated_scripts,
    representative_scripts,
    script,
)
from tests.test_hamcp209_fast_dependency_index import (  # noqa: E402
    _AnalysisRest,
    _bearer,
    _jwt_shape,
    _leaks,
    _planted_values,
    _serialized,
)


SECRET = "synthetic-not-a-credential"
BLUEPRINT_READER = "ha_mcp_engineering.dependency.provider._read_blueprint_with_status"


def provider_for(ha: SyntheticScriptHome, *, batch: bool = True, reader=None):
    reader = reader or SyntheticScriptReader(ha)
    websocket = ha.websocket if batch else ha.websocket_without_batch
    provider = DirectHaDependencyProvider(
        ha.rest, websocket, secret=SECRET, script_reader_factory=lambda: reader,
    )
    return provider, reader


async def scan(provider):
    with patch(BLUEPRINT_READER, side_effect=read_blueprint):
        return await provider.scan()


def _rows(items) -> list[str]:
    return sorted(json.dumps(asdict(item), sort_keys=True, default=str) for item in items)


def script_view(diagnostics) -> dict:
    """Everything entity_dependency_analysis consumes, except timing and source."""

    coverage = diagnostics.coverage.public()
    return {
        "findings": _rows(diagnostics.findings),
        "dynamic_references": _rows(diagnostics.dynamic_references),
        "call_findings": _rows(diagnostics.call_findings),
        "call_gaps": list(diagnostics.call_gaps),
        "coverage": {
            key: value for key, value in coverage.items()
            if key not in {"duration_ms", "policy", "provider"}
        },
    }


def warnings_of(diagnostics) -> list[str]:
    return list(diagnostics.coverage.warnings)


class ScriptParityTests(unittest.IsolatedAsyncioTestCase):
    async def pair(self, automations, scripts):
        loaded_ha = SyntheticScriptHome(deepcopy(automations), deepcopy(scripts))
        stored_ha = SyntheticScriptHome(deepcopy(automations), deepcopy(scripts))
        loaded_provider, loaded_reader = provider_for(loaded_ha)
        stored_provider, stored_reader = provider_for(stored_ha, batch=False)
        loaded = await scan(loaded_provider)
        stored = await scan(stored_provider)
        return loaded, loaded_reader, stored, stored_reader

    async def test_representative_scripts_are_identical(self):
        loaded, loaded_reader, stored, stored_reader = await self.pair(
            representative_automations(), representative_scripts(),
        )
        self.assertEqual(script_view(loaded.script_diagnostics), script_view(stored.script_diagnostics))
        self.assertTrue(loaded.script_diagnostics.findings)
        self.assertTrue(loaded.script_diagnostics.dynamic_references)
        self.assertTrue(loaded.script_diagnostics.call_findings)
        # Only the documented fallbacks read the delegated stored route.
        self.assertEqual(sorted(loaded_reader.calls), [
            "script.synthetic_script_blueprint",
            "script.synthetic_script_nested_caller",
            "script.synthetic_script_unavailable",
        ])
        self.assertEqual(len(stored_reader.calls), 8)
        self.assertEqual(loaded.profile["script_stored_config_fallback_reasons"], {
            "blueprint_instance": 1, "blueprint_reference_nested": 1, "loaded_entity_unavailable": 1,
        })
        # Without a batch client every script reads stored; an unavailable
        # entity is still named as such rather than as unsupported.
        self.assertEqual(stored.profile["script_stored_config_fallback_reasons"], {
            "loaded_entity_unavailable": 1, "loaded_source_unsupported": 7,
        })

    async def test_bulk_fixture_is_identical_with_no_delegated_read(self):
        loaded, loaded_reader, stored, _ = await self.pair(generated_automations(113), generated_scripts(5))
        self.assertEqual(script_view(loaded.script_diagnostics), script_view(stored.script_diagnostics))
        self.assertEqual(loaded_reader.calls, [])
        self.assertEqual(len(loaded.script_diagnostics.findings), 5)

    async def test_automation_index_and_fingerprint_are_unchanged(self):
        from tests.test_hamcp209_fast_dependency_index import index_view

        loaded, _, stored, _ = await self.pair(representative_automations(), representative_scripts())
        self.assertEqual(index_view(loaded), index_view(stored))
        snapshots = []
        for result in (loaded, stored):
            class Fixed:
                provider_id = "direct_ha_api"

                async def scan(self, result=result):
                    return result

            snapshot, _, _ = await DependencyIndex(Fixed()).get(refresh=True)
            snapshots.append(snapshot.fingerprint)
        self.assertEqual(snapshots[0], snapshots[1])

    async def test_blueprint_scripts_are_analyzed_from_stored_input_form(self):
        loaded, _, _, _ = await self.pair([], representative_scripts())
        diagnostics = loaded.script_diagnostics
        blueprint = [item for item in diagnostics.findings if item.source_id == "synthetic_script_blueprint"]
        # The stored use_blueprint input, never the substituted loaded body.
        self.assertEqual(
            [(item.config_path, item.blueprint_path, item.blueprint_input) for item in blueprint],
            [("$.use_blueprint.input.notify_target", "synthetic/script_notify.yaml", "notify_target")],
        )
        self.assertEqual(diagnostics.provider_for("synthetic_script_blueprint"), "upstream_read_gateway")
        self.assertEqual(diagnostics.provider_for("synthetic_script_nested_caller"), "upstream_read_gateway")
        self.assertIn("script_blueprint_body_unresolved: 2", warnings_of(diagnostics))

    async def test_loaded_scripts_are_attributed_to_the_direct_source(self):
        loaded, _, _, _ = await self.pair([], representative_scripts())
        diagnostics = loaded.script_diagnostics
        for key in ("plain", "templated", "dynamic", "caller", "categorized"):
            self.assertEqual(diagnostics.provider_for(f"synthetic_script_{key}"), "direct_ha_api", key)
        self.assertEqual(diagnostics.coverage.provider, "direct_ha_api+upstream_read_gateway")
        self.assertIn("script_config_source=mixed; loaded=5; stored=3", diagnostics.coverage.policy)
        self.assertIn(
            "stored fallback reasons: blueprint_instance=1, blueprint_reference_nested=1, "
            "loaded_entity_unavailable=1",
            diagnostics.coverage.policy,
        )

    async def test_injected_category_does_not_change_the_analysis(self):
        loaded, _, stored, _ = await self.pair([], [
            script("synthetic_script_categorized", {"sequence": [
                {"action": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"}},
            ]}, category="synthetic_category"),
        ])
        self.assertEqual(script_view(loaded.script_diagnostics), script_view(stored.script_diagnostics))


class ScriptBoundedRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_113_automations_and_5_scripts_use_one_connection_and_no_delegated_read(self):
        ha = SyntheticScriptHome(generated_automations(113), generated_scripts(5))
        provider, reader = provider_for(ha)
        result = await scan(provider)
        profile = result.profile
        self.assertEqual(len(ha.batch_calls), 1)
        self.assertEqual(len(ha.batch_calls[0]), 2 * 113 + 2 * 5)
        self.assertEqual(reader.calls, [])
        self.assertEqual(profile["request_count_by_operation"]["loaded_config_batch"], 1)
        self.assertEqual(profile["request_count_by_operation"]["delegated_script_configuration"], 0)
        self.assertNotIn("loaded_automation_config_batch", profile["request_count_by_operation"])
        self.assertEqual(profile["loaded_config_connection_count"], 1)
        self.assertEqual(profile["loaded_config_command_count"], 236)
        self.assertEqual(profile["loaded_script_command_count"], 10)
        self.assertEqual(ha.rest_counts["automation_config_read"], 0)
        self.assertEqual(profile["script_config_source"], "loaded_websocket")
        self.assertEqual(profile["script_config_source_counts"], {
            "loaded_websocket": 5, "stored_delegated": 0,
        })

    async def test_script_payloads_are_fixed_read_shapes_for_available_candidates(self):
        scripts = representative_scripts() + [
            script("synthetic_script_registry_only", {"sequence": []}, state=None),
        ]
        ha = SyntheticScriptHome([], scripts)
        provider, _ = provider_for(ha)
        await scan(provider)
        self.assertEqual(len(ha.batch_calls), 1)
        payloads = ha.batch_calls[0]
        self.assertTrue(all(batch_command_allowed(item) for item in payloads))
        commanded = sorted({item.get("entity_id") or item.get("item_id") for item in payloads})
        # Unavailable and registry-only scripts are not commanded.
        self.assertEqual(commanded, sorted(
            f"script.synthetic_script_{key}"
            for key in ("plain", "templated", "dynamic", "caller", "blueprint", "nested_caller", "categorized")
        ))
        for entity_id in commanded:
            self.assertIn({"type": "script/config", "entity_id": entity_id}, payloads)
            self.assertIn({"type": "search/related", "item_type": "script", "item_id": entity_id}, payloads)
        self.assertEqual(len(payloads), 2 * len(commanded))

    async def test_delegated_fallback_reads_stay_bounded(self):
        scripts = [
            script(f"synthetic_bp_{index:02d}", {"use_blueprint": {"path": "synthetic/x.yaml", "input": {}}},
                   loaded={"sequence": []}, blueprint="synthetic/x.yaml")
            for index in range(24)
        ]
        ha = SyntheticScriptHome([], scripts, delegated_seconds_per_read=0.001)
        provider, reader = provider_for(ha)
        await scan(provider)
        self.assertEqual(len(reader.calls), 24)
        self.assertLessEqual(reader.max_in_flight, 8)
        self.assertLessEqual(BATCH_WINDOW, 16)
        self.assertGreaterEqual(MAX_BATCH_COMMANDS, 2 * (1_000 + 1_000))

    async def test_scripts_without_automations_still_use_one_batch(self):
        ha = SyntheticScriptHome([], generated_scripts(3))
        provider, reader = provider_for(ha)
        result = await scan(provider)
        self.assertEqual(len(ha.batch_calls), 1)
        self.assertEqual(reader.calls, [])
        self.assertEqual(result.profile["loaded_script_command_count"], 6)

    async def test_no_script_reader_dispatches_no_script_command(self):
        ha = SyntheticScriptHome(generated_automations(2), generated_scripts(3))
        provider = DirectHaDependencyProvider(ha.rest, ha.websocket, secret=SECRET)
        result = await scan(provider)
        self.assertEqual(len(ha.batch_calls), 1)
        self.assertTrue(all(item["type"] != "script/config" for item in ha.batch_calls[0]))
        self.assertTrue(all(item.get("item_type") != "script" for item in ha.batch_calls[0]))
        self.assertIsNone(result.script_diagnostics)
        self.assertEqual(result.profile["loaded_script_command_count"], 0)

    async def test_failed_reader_creation_dispatches_no_script_command(self):
        ha = SyntheticScriptHome([], generated_scripts(3))

        def broken_factory():
            raise RuntimeError("synthetic reader unavailable")

        provider = DirectHaDependencyProvider(
            ha.rest, ha.websocket, secret=SECRET, script_reader_factory=broken_factory,
        )
        result = await scan(provider)
        self.assertEqual(ha.batch_calls, [])
        self.assertIn("script_provider_unavailable: 1", warnings_of(result.script_diagnostics))
        self.assertEqual(result.script_diagnostics.coverage.provider, "none")


class ScriptFallbackAccountingTests(unittest.IsolatedAsyncioTestCase):
    def assert_accounted(self, result):
        profile = result.profile
        counts = profile["script_config_source_counts"]
        reasons = profile["script_stored_config_fallback_reasons"]
        self.assertEqual(sum(reasons.values()), counts["stored_delegated"])
        self.assertTrue(set(reasons) <= SCRIPT_LOADED_FALLBACK_REASONS, reasons)
        policy = result.script_diagnostics.coverage.policy
        for reason, count in reasons.items():
            self.assertIn(f"{reason}={count}", policy)

    async def test_each_per_script_reason_is_reported(self):
        scripts = representative_scripts() + [
            script("synthetic_script_registry_only", {"sequence": [
                {"action": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"}},
            ]}, state=None),
            script("synthetic_script_not_found", {"sequence": []}),
            script("synthetic_script_malformed", {"sequence": []}),
            script("synthetic_script_search_failed", {"sequence": []}),
        ]
        ha = SyntheticScriptHome([], scripts)
        ha.batch_overrides[("script/config", "script.synthetic_script_not_found")] = (False, "not_found")
        ha.batch_overrides[("script/config", "script.synthetic_script_malformed")] = (True, {"config": "x"})
        ha.batch_overrides[("search/related", "script.synthetic_script_search_failed")] = (False, "unknown_error")
        provider, reader = provider_for(ha)
        result = await scan(provider)
        self.assertEqual(result.profile["script_stored_config_fallback_reasons"], {
            "blueprint_instance": 1,
            "blueprint_reference_nested": 1,
            "loaded_config_unreadable": 3,
            "loaded_entity_unavailable": 2,
        })
        self.assertEqual(result.profile["script_config_source_counts"], {
            "loaded_websocket": 5, "stored_delegated": 7,
        })
        self.assertEqual(len(reader.calls), 7)
        self.assert_accounted(result)
        # The unreadable loaded reads are partial, never silent.
        self.assertEqual(result.profile["script_loaded_config_unavailable"], 3)
        self.assertIn(f"{LOADED_CONFIG_UNAVAILABLE}: 3", warnings_of(result.script_diagnostics))

    async def test_stable_reasons_add_no_loaded_failure(self):
        ha = SyntheticScriptHome([], representative_scripts())
        provider, _ = provider_for(ha)
        result = await scan(provider)
        self.assertEqual(result.profile["script_loaded_config_unavailable"], 0)
        self.assertFalse(any(
            item.startswith(LOADED_CONFIG_UNAVAILABLE) for item in warnings_of(result.script_diagnostics)
        ))
        self.assert_accounted(result)

    async def test_no_batch_client_reads_stored_as_before(self):
        ha = SyntheticScriptHome([], generated_scripts(4))
        provider, reader = provider_for(ha, batch=False)
        result = await scan(provider)
        self.assertEqual(len(reader.calls), 4)
        self.assertEqual(result.profile["script_stored_config_fallback_reasons"], {"loaded_source_unsupported": 4})
        self.assertEqual(result.profile["script_config_source"], "stored_delegated")
        self.assertEqual(result.script_diagnostics.coverage.provider, "upstream_read_gateway")
        # Unsupported is the pre-HAMCP-212 path, not a loaded failure.
        self.assertEqual(result.profile["script_loaded_config_unavailable"], 0)
        self.assert_accounted(result)

    async def test_package_script_is_readable_from_loaded_only(self):
        package = {"sequence": [
            {"action": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"}},
        ]}
        ha = SyntheticScriptHome([], [script("synthetic_package_script", None, loaded=package)])
        provider, reader = provider_for(ha)
        result = await scan(provider)
        self.assertEqual(reader.calls, [])
        self.assertEqual(
            [item.source_id for item in result.script_diagnostics.findings], ["synthetic_package_script"],
        )
        self.assertEqual(result.script_diagnostics.coverage.failed_item_count, 0)
        # The stored route alone could not read it (scripts.yaml has no entry).
        stored_ha = SyntheticScriptHome([], [script("synthetic_package_script", None, loaded=package)])
        stored_provider, _ = provider_for(stored_ha, batch=False)
        stored = await scan(stored_provider)
        self.assertEqual(stored.script_diagnostics.findings, ())
        self.assertIn("resource_not_found: 1", warnings_of(stored.script_diagnostics))


class ScriptPartialOnFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_failure_is_partial_and_reads_stored(self):
        ha = SyntheticScriptHome(generated_automations(2), generated_scripts(3))
        ha.batch_failure = HomeAssistantUnavailableError()
        provider, reader = provider_for(ha)
        result = await scan(provider)
        diagnostics = result.script_diagnostics
        self.assertEqual(len(reader.calls), 3)
        self.assertEqual(result.profile["script_stored_config_fallback_reasons"], {"loaded_source_unavailable": 3})
        self.assertEqual(result.profile["script_loaded_config_unavailable"], 3)
        self.assertIn(f"{LOADED_CONFIG_UNAVAILABLE}: 3", warnings_of(diagnostics))
        self.assertGreaterEqual(diagnostics.coverage.failed_item_count, 3)
        self.assertEqual(diagnostics.coverage.completeness, "partial")
        # The stored evidence is still reported; the loaded failure is disclosed.
        self.assertEqual(len(diagnostics.findings), 3)
        # One whole-batch failure covers automations too (shared connection).
        automation = next(item for item in result.coverage if item.source_type == "automation")
        self.assertEqual(automation.completeness, "partial")

    async def test_incomplete_batch_result_is_a_whole_source_failure(self):
        ha = SyntheticScriptHome([], generated_scripts(2))
        provider, reader = provider_for(ha)

        async def short_batch(payloads):
            ha.batch_calls.append(payloads)
            return [(True, {"config": {"sequence": []}})]

        provider.websocket_client = SimpleNamespace(command=ha.websocket.command, command_batch=short_batch)
        result = await scan(provider)
        self.assertEqual(len(reader.calls), 2)
        self.assertEqual(result.profile["script_stored_config_fallback_reasons"], {"loaded_source_unavailable": 2})
        self.assertIn(f"{LOADED_CONFIG_UNAVAILABLE}: 2", warnings_of(result.script_diagnostics))

    async def test_loaded_failure_with_stored_failure_is_unavailable_evidence(self):
        ha = SyntheticScriptHome([], [script("synthetic_script_lost", {"sequence": [
            {"action": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"}},
        ]})])
        ha.batch_overrides[("script/config", "script.synthetic_script_lost")] = (False, "not_found")
        ha.script_read_failures["script.synthetic_script_lost"] = "upstream_unavailable"
        provider, _ = provider_for(ha)
        result = await scan(provider)
        diagnostics = result.script_diagnostics
        self.assertEqual(diagnostics.findings, ())
        self.assertEqual(diagnostics.coverage.completeness, "unavailable")
        self.assertGreaterEqual(diagnostics.coverage.failed_item_count, 1)

    async def test_failed_loaded_read_never_reports_complete(self):
        for override in (
            (("script/config", "script.synthetic_bulk_script_00"), (False, "unknown_error")),
            (("script/config", "script.synthetic_bulk_script_00"), (True, {"config": None})),
            (("search/related", "script.synthetic_bulk_script_00"), (True, ["not", "a", "mapping"])),
        ):
            with self.subTest(override=override):
                ha = SyntheticScriptHome([], generated_scripts(1))
                ha.batch_overrides[override[0]] = override[1]
                provider, _ = provider_for(ha)
                result = await scan(provider)
                self.assertEqual(result.profile["script_loaded_config_unavailable"], 1)
                self.assertIn(f"{LOADED_CONFIG_UNAVAILABLE}: 1", warnings_of(result.script_diagnostics))
                self.assertEqual(
                    result.profile["script_stored_config_fallback_reasons"], {"loaded_config_unreadable": 1},
                )

    async def test_analysis_discloses_loaded_failure(self):
        ha = SyntheticScriptHome([], generated_scripts(2))
        ha.batch_failure = HomeAssistantUnavailableError()
        provider, _ = provider_for(ha)
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            output = await EntityDependencyAnalysisService(DependencyIndex(provider)).analyze(
                entity_id="light.synthetic_bulk_0000", include_indirect=True,
            )
        coverage = next(item for item in output.data["source_coverage"] if item["source_type"] == "script")
        self.assertEqual(coverage["completeness"], "partial")
        self.assertIn(f"{LOADED_CONFIG_UNAVAILABLE}: 2", coverage["warnings"])
        self.assertFalse(output.data["overview"]["coverage_complete"])


class ScriptPendingWriteJournalTests(unittest.IsolatedAsyncioTestCase):
    def written(self, ha, key, target):
        item = ha.scripts[f"script.{key}"]
        new_config = deepcopy(item["stored"])
        new_config["sequence"][0]["target"]["entity_id"] = target
        item["stored"] = new_config

    def targets(self, result, key):
        return {item.target_entity_id for item in result.script_diagnostics.findings if item.source_id == key}

    async def test_unreloaded_write_reads_stored_until_reload(self):
        ha = SyntheticScriptHome([], generated_scripts(3))
        provider, reader = provider_for(ha)
        key = "synthetic_bulk_script_01"
        provider.note_script_write(key)
        self.written(ha, key, "light.synthetic_written")

        result = await scan(provider)
        self.assertEqual(self.targets(result, key), {"light.synthetic_written"})
        self.assertEqual(result.profile["script_stored_config_fallback_reasons"], {"pending_write_reload": 1})
        self.assertEqual(result.script_diagnostics.provider_for(key), "upstream_read_gateway")
        self.assertEqual(result.profile["script_pending_write_journal"]["pending_at_build"], 1)
        self.assertIn(key, provider._pending_script_writes)

        ha.reload_script(f"script.{key}")
        result = await scan(provider)
        self.assertEqual(self.targets(result, key), {"light.synthetic_written"})
        self.assertEqual(result.profile["script_stored_config_fallback_reasons"], {})
        self.assertEqual(result.profile["script_pending_write_journal"]["verified_cleared"], 1)
        self.assertEqual(result.script_diagnostics.provider_for(key), "direct_ha_api")
        self.assertEqual(provider._pending_script_writes, {})

        reader.calls.clear()
        await scan(provider)
        self.assertEqual(reader.calls, [])

    async def test_written_script_not_loaded_is_unreconciled(self):
        ha = SyntheticScriptHome([], generated_scripts(2))
        provider, reader = provider_for(ha)
        ha.stored_only_scripts["synthetic_new_script"] = {"sequence": []}
        provider.note_script_write("synthetic_new_script")
        result = await scan(provider)
        self.assertIn("script.synthetic_new_script", reader.calls)
        self.assertIn(f"{PENDING_WRITE_UNRECONCILED}: 1", warnings_of(result.script_diagnostics))
        self.assertEqual(result.profile["script_pending_write_journal"]["unreconciled"], 1)
        self.assertIn("synthetic_new_script", provider._pending_script_writes)

    async def test_deleted_pending_script_clears_on_not_found(self):
        ha = SyntheticScriptHome([], generated_scripts(2))
        provider, _ = provider_for(ha)
        provider.note_script_write("synthetic_deleted_script")
        result = await scan(provider)
        self.assertEqual(provider._pending_script_writes, {})
        self.assertFalse(any(
            item.startswith(PENDING_WRITE_UNRECONCILED) for item in warnings_of(result.script_diagnostics)
        ))

    async def test_unreadable_pending_absent_script_is_not_cleared(self):
        ha = SyntheticScriptHome([], generated_scripts(1))
        provider, _ = provider_for(ha)
        provider.note_script_write("synthetic_absent_script")
        ha.script_read_failures["script.synthetic_absent_script"] = "upstream_unavailable"
        result = await scan(provider)
        self.assertIn("synthetic_absent_script", provider._pending_script_writes)
        self.assertIn(f"{PENDING_WRITE_UNRECONCILED}: 1", warnings_of(result.script_diagnostics))

    async def test_stored_read_failure_for_pending_write_is_not_cleared(self):
        ha = SyntheticScriptHome([], generated_scripts(2))
        provider, _ = provider_for(ha)
        key = "synthetic_bulk_script_00"
        provider.note_script_write(key)
        ha.script_read_failures[f"script.{key}"] = "upstream_unavailable"
        result = await scan(provider)
        self.assertEqual(self.targets(result, key), set())
        self.assertIn(key, provider._pending_script_writes)
        self.assertIn(f"{PENDING_WRITE_UNRECONCILED}: 1", warnings_of(result.script_diagnostics))
        self.assertIn("script_provider_error: 1", warnings_of(result.script_diagnostics))

    async def test_unchanged_write_clears_when_stored_matches_loaded(self):
        ha = SyntheticScriptHome([], generated_scripts(2))
        provider, reader = provider_for(ha)
        key = "synthetic_bulk_script_00"
        provider.note_script_write(key)
        result = await scan(provider)
        self.assertEqual(reader.calls, [f"script.{key}"])
        self.assertEqual(result.profile["script_stored_config_fallback_reasons"], {})
        self.assertEqual(result.script_diagnostics.provider_for(key), "direct_ha_api")
        self.assertEqual(provider._pending_script_writes, {})

    async def test_rewrite_during_build_is_retained(self):
        ha = SyntheticScriptHome([], generated_scripts(2))
        provider, reader = provider_for(ha)
        key = "synthetic_bulk_script_00"
        provider.note_script_write(key)
        original = reader.read

        async def read_and_rewrite(entity_id):
            provider.note_script_write(key)
            return await original(entity_id)

        reader.read = read_and_rewrite
        await scan(provider)
        self.assertIn(key, provider._pending_script_writes)

    async def test_journal_overflow_is_sticky_and_always_partial(self):
        ha = SyntheticScriptHome([], generated_scripts(2))
        provider, reader = provider_for(ha)
        for index in range(MAX_PENDING_SCRIPT_WRITES + 1):
            provider.note_script_write(f"synthetic_overflow_{index:03d}")
        self.assertTrue(provider._pending_script_write_overflow)
        result = await scan(provider)
        warnings = warnings_of(result.script_diagnostics)
        self.assertTrue(any(item.startswith(PENDING_WRITE_UNRECONCILED) for item in warnings), warnings)
        journal = result.profile["script_pending_write_journal"]
        self.assertTrue(journal["overflow_at_build"])
        self.assertEqual(journal["overflow_write_count"], 1)
        # Overflow sends every candidate through the stored read.
        self.assertIn("script.synthetic_bulk_script_00", reader.calls)
        ha.stored_only_scripts.clear()
        provider._pending_script_writes.clear()
        result = await scan(provider)
        self.assertTrue(provider._pending_script_write_overflow)
        self.assertIn(f"{PENDING_WRITE_UNRECONCILED}: 1", warnings_of(result.script_diagnostics))

    async def test_overflow_routed_package_script_keeps_loaded_config(self):
        package = {"sequence": [
            {"action": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"}},
        ]}
        ha = SyntheticScriptHome([], [script("synthetic_package_script", None, loaded=package)])
        provider, _ = provider_for(ha)
        provider._pending_script_write_overflow = True
        result = await scan(provider)
        self.assertEqual(self.targets(result, "synthetic_package_script"), {"light.synthetic_kitchen"})
        self.assertEqual(result.script_diagnostics.provider_for("synthetic_package_script"), "direct_ha_api")
        self.assertIn(f"{PENDING_WRITE_UNRECONCILED}: 1", warnings_of(result.script_diagnostics))

    async def test_script_and_automation_journals_are_separate(self):
        ha = SyntheticScriptHome(generated_automations(1), generated_scripts(1))
        provider, _ = provider_for(ha)
        provider.note_script_write("synthetic_bulk_script_00")
        provider.note_automation_write("synthetic_bulk_0000")
        self.assertEqual(set(provider._pending_script_writes), {"synthetic_bulk_script_00"})
        self.assertEqual(set(provider._pending_writes), {"synthetic_bulk_0000"})

    async def test_runtime_records_script_writes_on_the_configured_provider(self):
        runtime = DependencyAnalysisRuntime()
        runtime.note_script_write("synthetic_unconfigured")  # no-op
        ha = SyntheticScriptHome([], [])
        runtime.configure(ha.rest, ha.websocket, secret=SECRET)
        runtime.note_script_write("synthetic_bulk_script_00")
        self.assertIn("synthetic_bulk_script_00", runtime.require().index.provider._pending_script_writes)
        await runtime.shutdown()


class GovernedScriptWriteHookTests(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(self, journal):
            self.journal = journal
            self.calls = []

        async def request(self, method, path, body=None, **kwargs):
            # The journal entry must exist before the write is dispatched.
            self.calls.append((method, str(path), list(self.journal)))
            return {"result": "ok"}

    async def test_resource_gateway_journals_scripts_before_dispatch(self):
        from ha_mcp_engineering.governance.resources import ConfigurationResourceGateway

        automation_journal: list[str] = []
        script_journal: list[str] = []
        client = self.Client(script_journal)
        gateway = ConfigurationResourceGateway(client, object())
        with patch(
            "ha_mcp_engineering.dependency.DEPENDENCY_ANALYSIS.note_automation_write",
            side_effect=automation_journal.append,
        ), patch(
            "ha_mcp_engineering.dependency.DEPENDENCY_ANALYSIS.note_script_write",
            side_effect=script_journal.append,
        ):
            await gateway._write_rest_configuration("script", "synthetic_script", {"sequence": []})
            await gateway._write_rest_configuration(
                "automation", "synthetic_hook", {"id": "synthetic_hook", "trigger": [], "action": []}
            )
        self.assertEqual(script_journal, ["synthetic_script"])
        self.assertEqual(automation_journal, ["synthetic_hook"])
        self.assertEqual(client.calls[0], ("POST", "/config/script/config/synthetic_script", ["synthetic_script"]))

    async def test_invalid_script_identifier_is_not_journaled(self):
        from ha_mcp_engineering.governance.resources import (
            ConfigurationMutationNotDispatchedError,
            ConfigurationResourceGateway,
        )

        journal: list[str] = []
        client = self.Client(journal)
        with patch(
            "ha_mcp_engineering.dependency.DEPENDENCY_ANALYSIS.note_script_write",
            side_effect=journal.append,
        ):
            with self.assertRaises(ConfigurationMutationNotDispatchedError):
                await ConfigurationResourceGateway(client, object())._write_rest_configuration(
                    "script", "../unsafe", {"sequence": []},
                )
        self.assertEqual(journal, [])
        self.assertEqual(client.calls, [])


class ScriptBatchAllowlistTests(unittest.TestCase):
    def test_only_fixed_script_read_shapes_are_admitted(self):
        self.assertTrue(batch_command_allowed({"type": "script/config", "entity_id": "script.synthetic"}))
        self.assertTrue(batch_command_allowed(
            {"type": "search/related", "item_type": "script", "item_id": "script.synthetic"}
        ))
        self.assertTrue(batch_command_allowed({"type": "script/config", "entity_id": "script." + "a" * 121}))
        refused = [
            {"type": "script/config", "entity_id": "automation.synthetic"},
            {"type": "script/config", "entity_id": "script.Synthetic"},
            {"type": "script/config", "entity_id": "script." + "a" * 122},
            {"type": "script/config", "entity_id": "script.synthetic", "extra": 1},
            {"type": "script/config", "entity_id": "script.synthetic", "id": 7},
            {"type": "script/config", "entity_id": "script.synthetic\n"},
            {"type": "script/config", "entity_id": ["script.synthetic"]},
            {"type": "script/config"},
            {"type": "search/related", "item_type": "script", "item_id": "automation.synthetic"},
            {"type": "search/related", "item_type": "automation", "item_id": "script.synthetic"},
            {"type": "search/related", "item_type": "scene", "item_id": "scene.synthetic"},
            {"type": "search/related", "item_type": "script", "item_id": "script.synthetic", "extra": 1},
            {"type": "automation/config", "entity_id": "script.synthetic"},
            {"type": "call_service", "domain": "script", "service": "reload"},
            {"type": "call_service", "domain": "script", "service": "turn_on"},
            {"type": "execute_script", "sequence": []},
            {"type": "config/script/delete", "entity_id": "script.synthetic"},
            {"type": "script/trace/list", "entity_id": "script.synthetic"},
        ]
        for payload in refused:
            with self.subTest(payload=payload):
                self.assertFalse(batch_command_allowed(payload))


class ScriptHealthProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_build_profile_reports_script_source_counts(self):
        ha = SyntheticScriptHome(representative_automations(), representative_scripts())
        provider, _ = provider_for(ha)
        index = DependencyIndex(provider)
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            await index.get(refresh=True)
        profile = index.health()["last_build_profile"]
        self.assertEqual(profile["script_config_source"], "mixed")
        self.assertEqual(profile["script_config_source_counts"], {"loaded_websocket": 5, "stored_delegated": 3})
        self.assertEqual(profile["script_stored_config_fallback_reasons"], {
            "blueprint_instance": 1, "blueprint_reference_nested": 1, "loaded_entity_unavailable": 1,
        })
        self.assertEqual(profile["script_loaded_config_unavailable"], 0)
        self.assertEqual(profile["script_pending_write_journal"], {
            "pending_at_build": 0, "overflow_at_build": False, "unreconciled": 0,
            "verified_cleared": 0, "overflow_write_count": 0,
        })
        self.assertEqual(profile["request_count_by_operation"]["delegated_script_configuration"], 3)
        self.assertEqual(profile["loaded_script_command_count"], 14)

    async def test_profile_without_script_reader_reports_none(self):
        ha = SyntheticScriptHome(generated_automations(1), [])
        provider = DirectHaDependencyProvider(ha.rest, ha.websocket, secret=SECRET)
        result = await scan(provider)
        self.assertEqual(result.profile["script_config_source"], "none")
        self.assertEqual(result.profile["script_config_source_counts"], {})


class ScriptAttributionTests(unittest.IsolatedAsyncioTestCase):
    async def analyze(self, scripts, entity_id, *, batch=True):
        ha = SyntheticScriptHome([], scripts)
        provider, _ = provider_for(ha, batch=batch)
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            output = await EntityDependencyAnalysisService(DependencyIndex(provider)).analyze(
                entity_id=entity_id, include_indirect=True,
            )
        return output.data

    async def test_path_evidence_names_the_configuration_source(self):
        data = await self.analyze(representative_scripts(), "light.synthetic_kitchen")
        graph = data["script_call_graph"]
        providers = {item["source_id"]: item["provider"] for item in graph["path_evidence"]}
        self.assertEqual(providers, {
            "synthetic_script_caller": "direct_ha_api", "synthetic_script_plain": "direct_ha_api",
        })
        self.assertEqual(graph["configuration_providers"], ["direct_ha_api", "upstream_read_gateway"])
        data = await self.analyze(representative_scripts(), "light.synthetic_hall")
        providers = {
            item["source_id"]: item["provider"] for item in data["script_call_graph"]["path_evidence"]
        }
        self.assertEqual(providers.get("synthetic_script_nested_caller"), "upstream_read_gateway")

    async def test_loaded_only_build_names_no_delegated_provider(self):
        data = await self.analyze(generated_scripts(2), "light.synthetic_bulk_0000")
        self.assertEqual(data["script_call_graph"]["configuration_providers"], ["direct_ha_api"])
        stored = await self.analyze(generated_scripts(2), "light.synthetic_bulk_0000", batch=False)
        self.assertEqual(
            stored["script_call_graph"]["configuration_providers"], ["direct_ha_api", "upstream_read_gateway"],
        )


_MARKERS = {
    name: f"hamcp212{name}plant"
    for name in (
        "jwt", "bearer", "password", "apikey", "userinfo", "webhook", "query",
        "template", "label", "target", "targetjwt", "safefield", "datakey",
        "variable", "field", "alias",
    )
}


def _secret_bearing_script(alias: str, helper: str) -> dict:
    """A loaded script whose ``!secret`` references Core already resolved."""

    m = _MARKERS
    credential_key = "pass" + "word"
    userinfo = "https://" + "synthetic" + ":" + m["userinfo"] + "@example.invalid/hook"
    query = "https://example.invalid/hook?" + "access_" + "token=" + m["query"]
    return {
        "alias": alias + " " + _bearer(m["alias"]),
        "fields": {"synthetic_field": {"default": _jwt_shape(m["field"])}},
        "variables": {"synthetic_variable": credential_key + "=" + m["variable"]},
        "sequence": [
            {
                "condition": "template",
                "value_template": (
                    "{{ is_state('" + helper + "', 'on') and '"
                    + credential_key + "=" + m["template"] + "' != '' }}"
                ),
            },
            {
                "action": "light.turn_on",
                "target": {"entity_id": "light.synthetic_kitchen"},
                "data": {credential_key: m["password"], "api_key": m["apikey"], "brightness": 10},
            },
            {
                "action": "light.turn_off",
                "target": {
                    "entity_id": "light.synthetic_hall",
                    "device_id": _bearer(m["target"]),
                    "label_id": _jwt_shape(m["targetjwt"]),
                },
                "data": {"transition": _bearer(m["safefield"]), m["datakey"]: "light.synthetic_hall"},
            },
            {
                "action": "light.turn_on",
                "target": {"entity_id": "{{ label_entities('" + credential_key + "=" + m["label"] + "') }}"},
            },
            {
                "action": "rest_command.synthetic",
                "data": {
                    "url": userinfo, "headers": _bearer(m["bearer"]),
                    "token_hint": _jwt_shape(m["jwt"]), "query": query,
                    "webhook_id": m["webhook"],
                },
            },
            {"action": "script.turn_on", "target": {"entity_id": "script.synthetic_secret_stored"}},
        ],
    }


class _ScriptAnalysisWebSocket(SyntheticScriptWebSocket):
    async def command(self, payload):
        if payload.get("type") in {"trace/list", "system_log/list"}:
            return []
        return await super().command(payload)


class ScriptResolvedSecretDisclosureTests(unittest.IsolatedAsyncioTestCase):
    """Loaded script configuration can hold resolved ``!secret`` values.

    One package script exists only in loaded form (the stored route has no
    entry), and one script's stored form holds the unresolved reference while
    its loaded form holds the resolved values. Every analysis family that
    reads the index runs at its most detailed level, and a helper-state plan
    is created and saved. No planted value may appear in any output or file.
    """

    HELPER = "input_boolean.beta37_exact_action"

    async def asyncSetUp(self):
        stored_reference = {"alias": "Synthetic secret stored", "sequence": [
            {"action": "rest_command.synthetic", "data": {"headers": "synthetic_secret_reference"}},
            {"action": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"}},
        ]}
        resolved = _secret_bearing_script("Synthetic secret stored", self.HELPER)
        resolved["sequence"][-1] = {"action": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"}}
        self.ha = SyntheticScriptHome(
            [record("automation.synthetic_gate", {
                "id": "synthetic_gate", "alias": "Synthetic gate",
                "trigger": [{"platform": "state", "entity_id": self.HELPER}],
                "action": [
                    {"service": "script.synthetic_secret_package"},
                    {"service": "light.turn_on", "target": {"entity_id": "light.synthetic_kitchen"},
                     "data": {"brightness": 10}},
                ],
            })],
            [
                script("synthetic_secret_package", None,
                       loaded=_secret_bearing_script("Synthetic secret package", self.HELPER)),
                script("synthetic_secret_stored", stored_reference, loaded=resolved),
            ],
        )
        self.rest = _AnalysisRest(self.ha)
        self.websocket = _ScriptAnalysisWebSocket(self.ha)
        self.reader = SyntheticScriptReader(self.ha)
        self.index = DependencyIndex(DirectHaDependencyProvider(
            self.rest, self.websocket, secret=SECRET, script_reader_factory=lambda: self.reader,
        ))

    def assert_no_marker(self, label: str, output) -> str:
        text = _serialized(output)
        leaked = sorted(name for name, marker in _MARKERS.items() if marker in text)
        self.assertEqual(leaked, [], f"{label} disclosed planted values")
        return text

    async def test_no_analysis_output_carries_a_resolved_script_secret(self):
        with patch(BLUEPRINT_READER, side_effect=read_blueprint):
            await self.run_families()

    async def run_families(self):
        snapshot, _, _ = await self.index.get(refresh=True)
        profile = snapshot.build_profile
        self.assertEqual(profile["script_config_source_counts"], {"loaded_websocket": 2, "stored_delegated": 0})
        self.assertEqual(self.reader.calls, [])

        outputs: dict[str, object] = {}
        dependency = EntityDependencyAnalysisService(self.index)
        for entity_id in (
            "light.synthetic_kitchen", "light.synthetic_hall", self.HELPER,
            "script.synthetic_secret_package", "script.synthetic_secret_stored",
        ):
            outputs[f"dependency {entity_id}"] = await dependency.analyze(
                entity_id=entity_id, detail_level="evidence", include_indirect=True,
            )
        impact = ChangeImpactAnalysisService(DirectHaImpactProvider(
            self.index, self.rest, self.websocket, secret=SECRET, ha_token=SECRET,
        ))
        for entity_id in ("light.synthetic_kitchen", "script.synthetic_secret_package"):
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
        handoff = HandoffGenerationService(EngineeringHandoffProvider(
            governance=SimpleNamespace(), incident=incident, dependency_index=self.index,
            rest_client=self.rest, health=SimpleNamespace(), secret=SECRET, ha_token=SECRET,
        ))
        handoff_values = {
            "handoff_type": "focused_review", "title": "",
            "focus_entity_ids": ["light.synthetic_kitchen", "script.synthetic_secret_package"],
            "automation_ids": ["synthetic_gate"], "change_plan_ids": [],
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
        outputs["helper risk"] = await HelperDependencyRiskService(self.index).assess(self.HELPER, refresh=True)
        outputs["health"] = self.index.health()

        texts = {label: self.assert_no_marker(label, output) for label, output in outputs.items()}
        # Not vacuous: both scripts are analyzed from loaded configuration.
        kitchen = texts["dependency light.synthetic_kitchen"]
        self.assertIn("synthetic_secret_package", kitchen)
        self.assertIn("synthetic_secret_stored", kitchen)
        self.assertIn("direct_ha_api", kitchen)

    async def test_saved_helper_plan_carries_no_resolved_script_secret(self):
        from ha_mcp_engineering.governance.service import ChangeGovernanceService
        from ha_mcp_engineering.governance.storage import ChangePlanRepository
        from tests.test_beta37_exact_helper_state import (
            Clock,
            FakeHelperStateGateway,
            UnusedLegacyGateway,
        )

        planted = {**_planted_values(), **_MARKERS}
        gateway = FakeHelperStateGateway()
        self.assertEqual(gateway.entity_id, self.HELPER)
        for key in ("synthetic_secret_package", "synthetic_secret_stored"):
            sequence = self.ha.scripts[f"script.{key}"]["loaded"]["sequence"]
            sequence.append({"action": "light.turn_on", "target": {
                "entity_id": "light.synthetic_kitchen", "label_id": planted["jwt_lowercased"],
            }, "data": {"transition": planted["random_base62"]}})
        with patch(BLUEPRINT_READER, side_effect=read_blueprint), \
                tempfile.TemporaryDirectory(prefix="hamcp212-plan-") as directory:
            service = ChangeGovernanceService(
                ChangePlanRepository(Path(directory) / "plans"),
                UnusedLegacyGateway(),
                now=Clock(),
                helper_state_gateway=gateway,
                helper_dependency_risk_reader=HelperDependencyRiskService(self.index).assess,
                plan_observability_cursor_key=b"synthetic-plan-key" * 2,
            )
            result = await service.create_helper_state_plan(entity_id=gateway.entity_id, desired_state="on")
            response = _serialized(result)
            stored = "\n".join(
                path.read_text(encoding="utf-8") for path in sorted(Path(directory).rglob("*.json"))
            )
        self.assertTrue(stored)
        self.assertEqual(gateway.dispatch_count, 0)
        self.assertEqual(self.reader.calls, [])
        for label, text in (("response", response), ("plan file", stored)):
            self.assertEqual(_leaks(text, planted), [], label)
            # Not vacuous: the dependent automation is in the saved plan.
            self.assertIn("synthetic_gate", text, label)


class ScriptReasonVocabularyTests(unittest.TestCase):
    def test_reason_vocabulary_is_closed(self):
        self.assertEqual(SCRIPT_LOADED_FALLBACK_REASONS, frozenset({
            "blueprint_instance", "blueprint_reference_nested", "loaded_entity_unavailable",
            "loaded_config_unreadable", "pending_write_reload", "loaded_source_unavailable",
            "loaded_source_unsupported",
        }))


if __name__ == "__main__":
    unittest.main()
