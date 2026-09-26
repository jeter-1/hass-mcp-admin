"""Offline diagnostic call graph; fixtures cannot execute a script or device."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.dependency import script_calls as calls
from ha_mcp_engineering.dependency.script_sources import collect_script_diagnostics
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService, select_dependency_findings
from ha_mcp_engineering.dependency.index import DependencyIndex
from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider
from ha_mcp_engineering.errors import GovernanceError
from ha_mcp_engineering.governance.helper_dependency import build_helper_dependency_risk_binding
from tests.test_script_dependency_analysis import Reader, registry, response, config
from tests.test_entity_dependency_analysis import FakeProvider, finding, scan, DirectProviderTests

MAPPING = {"script.renamed": "stored", "script.outer": "outer"}


def extract(body, **kwargs):
    return calls.extract_script_calls(body, source_type=kwargs.pop("source_type", "automation"),
        source_id="synthetic", source_entity_id="automation.synthetic", identities=MAPPING,
        secret="synthetic-private-token", **kwargs)


class CallExtractionTests(unittest.TestCase):
    def test_renamed_service_binds_storage_key_not_entity_suffix(self):
        for spelling in ("action", "service", "service_template"):
            edges, gaps = extract({"actions": [{spelling: "script.stored"}]})
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].target_entity_id, "script.renamed")
            self.assertEqual(edges[0].config_path, f"$.actions[0].{spelling}")
            self.assertEqual(edges[0].relation, "script_call")
            self.assertFalse(gaps)
        edges, gaps = extract({"action": [{"action": "script.renamed"}]})
        self.assertFalse(edges)
        self.assertIn("script_call_service_identity_unavailable", gaps)

    def test_explicit_turn_on_target_and_legacy_data(self):
        for section in ("target", "data", "data_template"):
            edges, gaps = extract({"action": {"service": "script.turn_on", section: {"entity_id": ["script.renamed", "script.outer"]}}})
            self.assertEqual([x.target_entity_id for x in edges], list(MAPPING))
            self.assertFalse(gaps)

    def test_all_supported_nested_action_positions(self):
        invoke = {"action": "script.stored"}
        body = {"action": [{"choose": [{"conditions": [], "sequence": [invoke]}], "default": [invoke]},
            {"if": [], "then": [invoke], "else": [invoke]},
            {"repeat": {"count": 2, "sequence": [invoke]}},
            {"parallel": [invoke, {"sequence": [invoke]}]}]}
        edges, gaps = extract(body)
        self.assertEqual(len(edges), 7)
        self.assertEqual(len({x.evidence_id for x in edges}), 7)
        self.assertFalse(gaps)

    def test_prose_conditions_variables_data_and_turn_off_are_not_calls(self):
        body = {"alias": "script.stored", "variables": {"action": "script.stored"},
            "trigger": [{"entity_id": "script.renamed"}], "action": [
                {"condition": "state", "entity_id": "script.renamed", "state": "on"},
                {"service": "notify.synthetic", "data": {"action": "script.stored", "sequence": [{"action": "script.stored"}]}},
                {"service": "script.turn_off", "target": {"entity_id": "script.renamed"}},
                {"service": "script.reload"}]}
        self.assertEqual(extract(body), ([], {}))

    def test_disabled_step_is_skipped_dynamic_enabled_remains_gap(self):
        edges, gaps = extract({"action": [{"enabled": False, "sequence": [{"action": "script.stored"}]},
            {"enabled": "{{ flag }}", "action": "script.stored"}]})
        self.assertFalse(edges)
        self.assertIn("script_call_action_enabled_unresolved", gaps)

    def test_dynamic_unknown_and_generic_calls_are_explicit_and_redacted(self):
        cases = [({"action": "{{ private_service }}"}, "service_dynamic"),
            ({"action": "script.unknown"}, "service_identity_unavailable"),
            ({"action": "script.turn_on", "target": {"entity_id": "{{ private_target }}"}}, "target_unresolved"),
            ({"action": "script.turn_on", "target": {"label_id": "private_label"}}, "selector_unresolved"),
            ({"action": "script.toggle"}, "toggle_unresolved"),
            ({"action": "homeassistant.turn_on"}, "generic_service_unresolved"),
            ({"action": "script.stored", "service": "script.outer"}, "service_ambiguous")]
        for step, reason in cases:
            with self.subTest(reason=reason):
                edges, gaps = extract({"action": [step]})
                self.assertFalse(edges)
                self.assertIn("script_call_" + reason, gaps)
                self.assertNotIn("private_", json.dumps(gaps))

    def test_multiple_selector_sources_are_not_combined_as_exact_calls(self):
        edges, gaps = extract({"action": [{"action": "script.turn_on", "target": {"entity_id": "script.outer"},
                                          "data": {"entity_id": "script.renamed"}}]})
        self.assertFalse(edges)
        self.assertIn("script_call_target_ambiguous", gaps)

    def test_selector_work_budget_spans_actions_and_omitted_targets_are_explicit(self):
        with patch.object(calls, "MAX_CALLS", 3):
            edges, gaps = extract({"action": [{"action": "script.turn_on", "target": {"entity_id": ["script.outer"] * 50}}] * 20})
        self.assertLessEqual(len(edges), 3)
        self.assertIn("script_call_target_items_exceeded", gaps)

    def test_reserved_service_collision_is_not_assumed_to_be_builtin(self):
        with patch.dict(MAPPING, {"script.collision": "turn_on"}):
            edges, gaps = extract({"action": [{"action": "script.turn_on", "target": {"entity_id": "script.outer"}}]})
        self.assertFalse(edges)
        self.assertIn("script_call_reserved_service_collision", gaps)

    def test_unknown_literal_target_remains_exact_reference_with_gap(self):
        edges, gaps = extract({"action": [{"action": "script.turn_on", "target": {"entity_id": "script.missing"}}]})
        self.assertEqual(edges[0].target_entity_id, "script.missing")
        self.assertIn("script_call_target_identity_unavailable", gaps)

    def test_ambiguous_roots_blueprints_and_malformed_steps_are_gaps(self):
        for body, reason in (({"action": [], "actions": []}, "action_root_ambiguous"),
                             ({"use_blueprint": {}}, "blueprint_body_unresolved"),
                             ({"action": [42]}, "action_invalid")):
            self.assertIn("script_call_" + reason, extract(body)[1])

    def test_node_depth_and_edge_bounds(self):
        body = {"action": [{"action": "script.stored"}] * 8}
        with patch.object(calls, "MAX_ACTION_NODES", 3):
            edges, gaps = extract(body)
        self.assertEqual(len(edges), 3)
        self.assertIn("script_call_action_nodes_exceeded", gaps)
        with patch.object(calls, "MAX_CALLS", 2):
            edges, gaps = extract(body)
        self.assertEqual(len(edges), 2)
        self.assertIn("script_call_edge_limit_exceeded", gaps)
        with patch.object(calls, "MAX_ACTION_DEPTH", 1):
            edges, gaps = extract({"action": [{"sequence": body["action"]}]})
        self.assertFalse(edges)
        self.assertIn("script_call_action_depth_exceeded", gaps)


async def graph(reader=None, automation=None):
    reader = reader or Reader({
        "script.renamed": response(key="stored"),
        "script.outer": response(key="outer", body={"sequence": [{"action": "script.stored"}]})})
    diagnostic = await collect_script_diagnostics([], [registry("script.renamed", "stored"), registry("script.outer", "outer")],
        registry_complete=True, reader_factory=lambda: reader, secret="synthetic-secret", concurrency=2,
        automation_documents=automation if automation is not None else (("caller", "automation.caller", {"actions": [{"action": "script.outer"}]}),))
    result = scan()
    result.script_diagnostics = diagnostic
    index = DependencyIndex(FakeProvider(result))
    service = EntityDependencyAnalysisService(index)
    return service, index, diagnostic


class GraphAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_edge_chain_filters_sources_without_hiding_intermediates(self):
        service, index, diagnostic = await graph()
        result = await service.analyze(entity_id="light.fixture", source_types=["automation"], include_indirect=True, max_depth=3, detail_level="evidence")
        self.assertEqual(result.data["overview"]["direct_reference_count"], 0)
        self.assertEqual(result.data["overview"]["indirect_reference_count"], 1)
        item = result.data["findings"][0]
        self.assertEqual(item["source_entity_id"], "automation.caller")
        self.assertEqual(item["depth"], 3)
        self.assertEqual(len(item["evidence_path"]), 3)
        self.assertEqual(item["confidence"], "exact_static_chain")
        proof = {edge["evidence_id"]: edge for edge in result.data["script_call_graph"]["path_evidence"]}
        self.assertEqual(set(proof), set(item["evidence_path"]))
        self.assertEqual({edge["provider"] for edge in proof.values()}, {"direct_ha_api", "upstream_read_gateway"})
        self.assertTrue(any(edge["source_id"] == "stored" and edge["source_entity_id"] == "script.renamed" for edge in proof.values()))
        self.assertTrue(result.partial)
        self.assertFalse(result.data["overview"]["coverage_complete"])
        self.assertEqual(result.data["assessment"]["rename_or_removal_status"], "references_found")
        self.assertFalse(index.snapshot.findings)
        self.assertEqual(result.data["script_call_graph"]["configuration_providers"], ["direct_ha_api", "upstream_read_gateway"])
        self.assertIn("8.5.0", result.data["script_call_graph"]["script_provider_policy"])

    async def test_direct_calls_and_old_entity_references_do_not_duplicate(self):
        service, index, diagnostic = await graph(automation=(("caller", "automation.caller", {"action": [{"action": "script.turn_on", "target": {"entity_id": "script.renamed"}}]}),))
        index.provider.scan_result.findings = [finding(target="script.renamed", source_id="caller", source_entity_id="automation.caller", path="$.action[0].target.entity_id", relation="service_target")]
        result = await service.analyze(entity_id="script.renamed", source_types=["automation"])
        self.assertEqual(result.data["overview"]["direct_reference_count"], 1)
        self.assertEqual(result.data["findings"][0]["relation"], "script_call")

    async def test_strict_depth_one_two_three_and_indirect_opt_in(self):
        service, _, _ = await graph()
        for depth, count in ((1, 0), (2, 1), (3, 2)):
            result = await service.analyze(entity_id="light.fixture", include_indirect=True, max_depth=depth)
            self.assertEqual(result.data["overview"]["indirect_reference_count"], count)
            self.assertTrue(all(x["depth"] <= depth for x in result.data["findings"]))
            if depth < 3:
                self.assertIn("script_call_max_depth_reached", result.data["script_call_graph"]["gaps"])
        direct = await service.analyze(entity_id="light.fixture", include_indirect=False)
        self.assertEqual(direct.data["overview"]["indirect_reference_count"], 0)

    async def test_state_observer_of_script_does_not_gain_script_effects(self):
        service, index, _ = await graph(automation=())
        index.provider.scan_result.findings = [finding(target="script.renamed", relation="trigger")]
        result = await service.analyze(entity_id="light.fixture", source_types=["automation"], include_indirect=True)
        self.assertFalse(result.data["findings"])
        self.assertEqual(result.data["assessment"]["rename_or_removal_status"], "unknown_due_to_incomplete_coverage")

    async def test_cycles_terminate_with_explicit_gap(self):
        reader = Reader({"script.renamed": response(key="stored", body={"sequence": [*config()["sequence"], {"action": "script.outer"}]}),
                         "script.outer": response(key="outer", body={"sequence": [{"action": "script.stored"}]})})
        service, _, _ = await graph(reader)
        result = await service.analyze(entity_id="light.fixture", include_indirect=True, max_depth=3)
        self.assertIn("script_call_cycle_detected", result.data["script_call_graph"]["gaps"])
        self.assertEqual(result.data["overview"]["indirect_reference_count"], 2)

    async def test_multiple_call_paths_keep_distinct_evidence(self):
        doc = (("caller", "automation.caller", {"action": [{"action": "script.stored"}, {"action": "script.outer"}]}),)
        service, _, _ = await graph(automation=doc)
        result = await service.analyze(entity_id="light.fixture", source_types=["automation"], include_indirect=True, max_depth=3)
        self.assertEqual([x["depth"] for x in result.data["findings"]], [2, 3])
        self.assertEqual(len({x["evidence_id"] for x in result.data["findings"]}), 2)

    async def test_processing_and_result_bounds_report_partial_paths(self):
        service, _, _ = await graph()
        for bound in ("MAX_GRAPH_STEPS", "MAX_GRAPH_RESULTS"):
            with patch.object(calls, bound, 1):
                result = await service.analyze(entity_id="light.fixture", include_indirect=True, max_depth=3)
            self.assertIn("script_call_traversal_limit_exceeded", result.data["script_call_graph"]["gaps"])
            self.assertTrue(result.partial)

    async def test_partial_read_preserves_other_paths_and_no_false_absence(self):
        reader = Reader({"script.renamed": response(key="stored"), "script.outer": {"success": False}})
        service, _, _ = await graph(reader, automation=(("a", "automation.a", {"action": [{"action": "script.stored"}]}),))
        result = await service.analyze(entity_id="light.fixture", source_types=["automation"], include_indirect=True)
        self.assertEqual(len(result.data["findings"]), 1)
        self.assertTrue(result.partial)
        self.assertTrue(any("script_provider_error" in warning for warning in result.warnings))

    async def test_cache_cursor_retirement_and_fingerprint(self):
        service, index, diagnostic = await graph()
        first = await service.analyze(entity_id="light.fixture", include_indirect=True, max_depth=3, limit=1)
        again = await service.analyze(entity_id="light.fixture", include_indirect=True, max_depth=3, limit=1)
        self.assertEqual(first.data["findings"], again.data["findings"])
        self.assertEqual(first.data["index"]["fingerprint"], again.data["index"]["fingerprint"])
        self.assertTrue(again.data["index"]["cache_hit"])
        cursor = first.data["pagination"]["next_cursor"]
        self.assertTrue(cursor)
        index.snapshot.script_diagnostics.authority_current.__self__.active = False
        with self.assertRaises(GovernanceError):
            await service.analyze(entity_id="light.fixture", include_indirect=True, max_depth=3, limit=1, cursor=cursor)
        result = await service.analyze(entity_id="light.fixture", include_indirect=True)
        self.assertFalse(result.data["findings"])
        self.assertTrue(result.partial)

    async def test_call_only_change_changes_diagnostic_fingerprint_not_shared_authority(self):
        service, index, before = await graph()
        other, other_index, after = await graph(automation=())
        await service.analyze(entity_id="light.fixture")
        await other.analyze(entity_id="light.fixture")
        self.assertNotEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(index.snapshot.fingerprint, other_index.snapshot.fingerprint)
        self.assertEqual(index.snapshot.obligations, other_index.snapshot.obligations)
        metadata = {"freshness": "current", "evidence_stale": False, "invalidated": False}
        self.assertEqual(build_helper_dependency_risk_binding(index.snapshot, entity_id="input_boolean.fixture", index_metadata=metadata),
                         build_helper_dependency_risk_binding(other_index.snapshot, entity_id="input_boolean.fixture", index_metadata=metadata))
        self.assertEqual(select_dependency_findings(index.snapshot.findings, "light.fixture", ["automation"], include_indirect=True, max_depth=3), ([], []))

    async def test_caller_entity_rename_is_bound_to_diagnostic_fingerprint(self):
        _, _, before = await graph(automation=(("a", "automation.before", {"action": [{"action": "script.stored"}]}),))
        _, _, after = await graph(automation=(("a", "automation.after", {"action": [{"action": "script.stored"}]}),))
        self.assertNotEqual(before.fingerprint, after.fingerprint)

    async def test_mapping_only_change_invalidates_diagnostic_identity(self):
        fingerprints = []
        for entity in ("script.before", "script.after"):
            reader = Reader({entity: response(key="stored", body={"sequence": []})})
            diagnostic = await collect_script_diagnostics([], [registry(entity, "stored")], registry_complete=True,
                reader_factory=lambda: reader, secret="synthetic", concurrency=1)
            fingerprints.append(diagnostic.fingerprint)
        self.assertNotEqual(*fingerprints)

    async def test_ambiguous_registry_key_never_creates_guessed_call(self):
        reader = Reader()
        result = await collect_script_diagnostics([], [registry("script.one", "stored"), registry("script.two", "stored")],
            registry_complete=True, reader_factory=lambda: reader, secret="synthetic", concurrency=1,
            automation_documents=(("a", "automation.a", {"action": [{"action": "script.stored"}]}),))
        self.assertFalse(reader.calls)
        self.assertFalse(result.call_findings)
        self.assertIn("script_call_service_identity_unavailable: 1", result.call_gaps)

    async def test_unavailable_partition_cannot_claim_no_indirect_dependencies(self):
        result = await EntityDependencyAnalysisService(DependencyIndex(FakeProvider(scan()))).analyze(
            entity_id="light.fixture", source_types=["automation"], include_indirect=True)
        self.assertTrue(result.partial)
        self.assertEqual(result.data["assessment"]["rename_or_removal_status"], "unknown_due_to_incomplete_coverage")
        self.assertEqual(result.data["script_call_graph"]["completeness"], "unavailable")

    async def test_direct_provider_reuses_existing_automation_reads(self):
        class Rest(DirectProviderTests.Rest):
            async def request(self, method, path):
                result = await super().request(method, path)
                if path.startswith("/config/automation/config/"):
                    result["action"] = [{"action": "script.stored"}]
                return result
        class WebSocket(DirectProviderTests.WebSocket):
            async def command(self, payload):
                rows = await super().command(payload)
                if payload["type"] == "config/entity_registry/list":
                    rows.append(registry("script.renamed", "stored"))
                return rows
        reader = Reader({"script.renamed": response(key="stored")})
        result = await DirectHaDependencyProvider(Rest(), WebSocket(), script_reader_factory=lambda: reader).scan()
        self.assertTrue(any(x.source_type == "automation" for x in result.script_diagnostics.call_findings))
        self.assertFalse(any(x.relation == "script_call" for x in result.findings))
        self.assertEqual(reader.calls, ["script.renamed"])
