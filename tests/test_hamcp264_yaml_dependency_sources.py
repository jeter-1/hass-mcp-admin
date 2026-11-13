"""HAMCP-264: YAML template and script references in the dependency index.

Design ``docs/HAMCP_264_YAML_READER_DEPENDENCY_PURPOSE_DESIGN.md``. These
tests prove, through the real reviewed YAML reader route of
``UpstreamReadGateway`` over a synthetic in-memory transport:

* the ``dependency_index`` purpose reads only its four fixed paths, under its
  own audit event, and refuses every other path before dispatch; the other
  purposes refuse its paths, and the collector refuses a reader captured for
  any other purpose with zero reads;
* a ``!secret`` or ``!env_var`` canary never appears in the analysis output,
  the snapshot fingerprints, the logs or the audit events;
* an oversize upstream result is refused and the row is ``unavailable``; a
  truncated result is ``partial``; no row is ever ``complete``;
* an absent, retired or failing reader gives a truthful ``unavailable`` row;
* UI template helpers are counted, never read, and keep the row partial;
* template and YAML script references appear in entity_dependency_analysis,
  change_impact_analysis and the plan disclosure;
* configuration_integrity_analysis keeps its template row ``not_supported``.

Every value is synthetic. No live dependency is used.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import replace
import json
import logging
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

from ha_mcp_engineering.dependency import yaml_sources as ys  # noqa: E402
from ha_mcp_engineering.dependency.bounded_sources import bound_table  # noqa: E402
from ha_mcp_engineering.dependency.global_sources import (  # noqa: E402
    PARTITION_SOURCE_TYPES,
    YAML_SOURCE_TYPES,
)
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.provider import DirectHaDependencyProvider  # noqa: E402
from ha_mcp_engineering.dependency.service import EntityDependencyAnalysisService  # noqa: E402
from ha_mcp_engineering.governance import global_reference_disclosure as grd  # noqa: E402
from ha_mcp_engineering.impact.provider import DirectHaImpactProvider  # noqa: E402
from ha_mcp_engineering.impact.service import ChangeImpactAnalysisService  # noqa: E402
from ha_mcp_engineering.integrity import provider as integrity_provider  # noqa: E402
from ha_mcp_engineering.integrity.service import ConfigurationIntegrityAnalysisService  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import (  # noqa: E402
    DEFINITION_YAML_PATHS,
    DEPENDENCY_YAML_PATHS,
    YAML_CONFIG_PATHS,
    YAML_READER_PURPOSES,
)
import hamcp162_synthetic as syn  # noqa: E402
from test_hamcp129_platform_reads import ok  # noqa: E402
from test_hamcp162_yaml_reader import TOOL, payload, yaml_gateway  # noqa: E402
from tests.hamcp209_fixtures import read_blueprint  # noqa: E402
from tests.test_hamcp209_fast_dependency_index import _AnalysisRest  # noqa: E402
from tests.test_hamcp227_global_sources import (  # noqa: E402
    ALL_SOURCES,
    BLUEPRINT_READER,
    SECRET,
    CountingWebSocket,
    Home,
    coverage_row,
    state,
)


SECRET_CANARY = syn.needle("264secretref")
ENV_CANARY = syn.needle("264envname")
ENV_DEFAULT_CANARY = syn.needle("264envdefault")
CANARIES = (SECRET_CANARY, ENV_CANARY, ENV_DEFAULT_CANARY)

TEMPLATE_TARGET = "sensor.synthetic_only_template"
TRIGGER_TARGET = "binary_sensor.synthetic_template_trigger"
PACKAGE_TARGET = "switch.synthetic_only_package"
SCRIPT_TARGET = "light.synthetic_only_yaml_script"
PACKAGE_SCRIPT_TARGET = "switch.synthetic_only_package_script"
TEMPLATE_ENTITY = "sensor.synthetic_template_sum"
UI_HELPER = "sensor.synthetic_ui_template_helper"
YAML_TARGETS = (TEMPLATE_TARGET, TRIGGER_TARGET, PACKAGE_TARGET, SCRIPT_TARGET, PACKAGE_SCRIPT_TARGET)

TEMPLATE_YAML = [
    {"sensor": [{
        "name": "Synthetic template sum",
        "unique_id": "synthetic_sum",
        "state": "{{ states('" + TEMPLATE_TARGET + "') | float(0) + 1 }}",
        "attributes": {"note": "!secret " + SECRET_CANARY,
                       "origin": "!env_var " + ENV_CANARY + " " + ENV_DEFAULT_CANARY},
    }]},
    {"triggers": [{"trigger": "state", "entity_id": TRIGGER_TARGET}],
     "binary_sensor": [{"name": "Synthetic triggered", "state": "{{ trigger.to_state.state == 'on' }}"}]},
]
SCRIPT_YAML = {
    "synthetic_yaml_only": {
        "alias": "Synthetic YAML only",
        "sequence": [{"action": "light.turn_on", "target": {"entity_id": SCRIPT_TARGET}}],
    },
}
PACKAGES_YAML = {
    "synthetic_pkg": {
        "template": [{"switch": [{
            "name": "Synthetic package switch",
            "state": "{{ is_state('" + PACKAGE_TARGET + "', 'on') }}",
            "turn_on": [{"action": "switch.turn_on", "target": {"entity_id": PACKAGE_TARGET}}],
            "turn_off": [{"action": "switch.turn_off", "target": {"entity_id": PACKAGE_TARGET}}],
        }]}],
        "script": {"synthetic_pkg_script": {
            "sequence": [{"action": "switch.toggle", "target": {"entity_id": PACKAGE_SCRIPT_TARGET}}],
        }},
    },
}
DOCUMENTS = {"template": TEMPLATE_YAML, "script": SCRIPT_YAML, "homeassistant.packages": PACKAGES_YAML}
TEMPLATE_REGISTRY = {"entity_id": TEMPLATE_ENTITY, "platform": "template", "unique_id": "synthetic_sum"}
UI_HELPER_REGISTRY = {"entity_id": UI_HELPER, "platform": "template", "unique_id": "synthetic-ui",
                      "config_entry_id": "synthetic_template_entry"}


def respond_with(documents):
    def respond(tool, arguments):
        path = arguments["yaml_path"]
        if path in documents:
            return ok(payload(documents[path], path))
        return ok(payload(None, path, found=False))
    return respond


def home(*, ui_helpers=False):
    extra = [state(entity) for entity in (*YAML_TARGETS, TEMPLATE_ENTITY)]
    registry = [TEMPLATE_REGISTRY] + ([UI_HELPER_REGISTRY] if ui_helpers else [])
    if ui_helpers:
        extra.append(state(UI_HELPER))
    return Home(scenes=False, extra_states=extra, extra_registry=registry)


async def build(ha, factory):
    rest = _AnalysisRest(ha)
    websocket = CountingWebSocket(ha)
    index = DependencyIndex(DirectHaDependencyProvider(
        rest, websocket, secret=SECRET, yaml_reader_factory=factory,
    ))
    with patch(BLUEPRINT_READER, side_effect=read_blueprint):
        snapshot, _, _ = await index.get(refresh=True)
    return index, snapshot, rest, websocket


class Captured(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


class YamlGatewayCase(unittest.IsolatedAsyncioTestCase):
    """A real gateway reader for the dependency_index purpose."""

    async def gateway_reader(self, documents=DOCUMENTS, **gateway_kwargs):
        self.gateway, self.transport = await yaml_gateway(**gateway_kwargs)
        self.transport.respond = respond_with(documents)
        self.audit = Mock()
        return lambda: self.gateway.yaml_config_reader(audit=self.audit, purpose=ys.READER_PURPOSE)

    def sent_paths(self):
        return [arguments["yaml_path"] for tool, arguments in self.transport.sent if tool == TOOL]

    def audit_events(self):
        return [call.args[0] for call in self.audit.write.call_args_list]

    async def analyses(self, index, rest, websocket, entity_id):
        dependency = await EntityDependencyAnalysisService(index).analyze(
            entity_id=entity_id, detail_level="evidence")
        provider = DirectHaImpactProvider(index, rest, websocket, secret=SECRET, ha_token=SECRET)
        impact = await ChangeImpactAnalysisService(provider).analyze(
            entity_id=entity_id, operation="remove_entity", detail_level="evidence", source_types=ALL_SOURCES)
        return dependency.data, impact.data


class PurposeBoundaryTests(YamlGatewayCase):
    """The purpose's paths, audit event and refusals, at the gateway."""

    def test_purpose_table_is_disjoint_where_it_must_be(self):
        self.assertEqual(YAML_READER_PURPOSES["dependency_index"],
                         (DEPENDENCY_YAML_PATHS, "dependency_index_yaml_read"))
        self.assertEqual(DEPENDENCY_YAML_PATHS,
                         frozenset({"template", "script", "homeassistant.packages", "homeassistant"}))
        events = [event for _, event in YAML_READER_PURPOSES.values()]
        self.assertEqual(len(events), len(set(events)))
        self.assertNotIn("script", DEFINITION_YAML_PATHS | YAML_CONFIG_PATHS)

    async def test_purpose_dispatches_only_its_paths_under_its_own_event(self):
        factory = await self.gateway_reader()
        reader = factory()
        self.assertIsNotNone(reader)
        self.assertEqual(reader.purpose, "dependency_index")
        for path in sorted(DEPENDENCY_YAML_PATHS):
            result = await reader.read(path)
            self.assertTrue(result["success"], result)
        self.assertEqual(self.sent_paths(), sorted(DEPENDENCY_YAML_PATHS))
        events = self.audit_events()
        self.assertEqual([event["event"] for event in events], ["dependency_index_yaml_read"] * 4)
        self.assertEqual([event["yaml_path"] for event in events], sorted(DEPENDENCY_YAML_PATHS))
        self.assertFalse(any(event["fallback_occurred"] for event in events))
        self.assertEqual({event["upstream_tool"] for event in events}, {TOOL})

    async def test_out_of_bounds_paths_are_refused_before_dispatch_and_audit(self):
        factory = await self.gateway_reader()
        reader = factory()
        for path in ("recorder", "sql", "group", "sensor", "binary_sensor", "utility_meter", "secrets",
                     "../secrets", "template.sensor", "script.synthetic_yaml_only", "Template", "",
                     None, "homeassistant.packages.synthetic_pkg", "automation"):
            with self.subTest(path=path):
                self.assertEqual(await reader.read(path), {
                    "success": False, "details": {"failure_category": "argument_validation"}})
        self.assertEqual(self.transport.sent, [])
        self.audit.write.assert_not_called()

    async def test_each_purpose_refuses_the_others_paths(self):
        await self.gateway_reader()
        dependency = self.gateway.yaml_config_reader(audit=self.audit, purpose="dependency_index")
        definitions = self.gateway.yaml_config_reader(audit=self.audit, purpose="configuration_definitions")
        recorder = self.gateway.yaml_config_reader(audit=self.audit)
        for path in ("sql", "group", "utility_meter", "sensor", "binary_sensor", "recorder"):
            self.assertFalse((await dependency.read(path))["success"])
        self.assertFalse((await definitions.read("script"))["success"])
        self.assertFalse((await recorder.read("script"))["success"])
        self.assertFalse((await recorder.read("template"))["success"])
        self.assertEqual(self.transport.sent, [])
        self.audit.write.assert_not_called()
        for purpose in ("Dependency_Index", "dependency-index", "", None, "dependency_index "):
            self.assertIsNone(self.gateway.yaml_config_reader(purpose=purpose))

    async def test_collector_refuses_a_reader_captured_for_another_purpose(self):
        await self.gateway_reader()
        for purpose in ("configuration_definitions", "recorder_settings"):
            with self.subTest(purpose=purpose):
                reader = self.gateway.yaml_config_reader(audit=self.audit, purpose=purpose)
                result = await ys.collect_yaml_sources(reader, [], registry_complete=True, secret=SECRET)
                self.assertEqual(result.profile["read_attempts"], 0)
                self.assertEqual(result.findings, ())
                for row in (result.template_coverage, result.script_coverage):
                    self.assertEqual((row.provider, row.completeness), ("none", "unavailable"))
                    self.assertIn(ys.UNAVAILABLE_WARNINGS["yaml_reader_purpose_mismatch"], row.warnings)
        self.assertEqual(self.transport.sent, [])
        self.audit.write.assert_not_called()


class SecretCanaryTests(YamlGatewayCase):
    """A !secret or !env_var reference never leaves the reader as a name or value."""

    async def test_canary_absent_from_output_fingerprints_logs_and_audit(self):
        factory = await self.gateway_reader()
        handler = Captured()
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            index, snapshot, rest, websocket = await build(home(), factory)
            outputs = []
            for target in (TEMPLATE_TARGET, TEMPLATE_ENTITY):
                outputs.extend(await self.analyses(index, rest, websocket, target))
            disclosure = grd.disclose(lambda: snapshot, (TEMPLATE_TARGET,))
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
        sources = snapshot.global_sources
        texts = {
            "outputs": json.dumps(outputs, default=str),
            "disclosure": json.dumps(disclosure, default=str),
            "findings": repr(sources.findings),
            "coverage": repr(sources.coverage),
            "fingerprints": json.dumps([sources.fingerprint, snapshot.fingerprint], default=str),
            "logs": "\n".join(handler.lines),
            "audit": json.dumps(self.audit_events(), default=str),
        }
        for canary in CANARIES:
            for channel, text in texts.items():
                with self.subTest(canary=canary, channel=channel):
                    self.assertNotIn(canary, text)
                    self.assertNotIn(canary.lower(), text.lower())
        template = sources.coverage_for("template")
        self.assertIn("secret_or_env_references_withheld: 2", template.warnings)
        # The analysis still ran on the withheld document.
        self.assertTrue(any(item.target_entity_id == TEMPLATE_TARGET for item in sources.findings))

    def test_withhold_references_replaces_markers_and_reference_text(self):
        counter = Counter()
        value = ys.withhold_references({
            "a": {"secret_ref": SECRET_CANARY},
            "b": [{"env_var_ref": ENV_CANARY, "default_redacted": True}],
            "c": "!secret " + SECRET_CANARY, "d": " !ENV_VAR " + ENV_CANARY, "e": "plain",
        }, counter)
        self.assertEqual(value, {"a": ys.REFERENCE_WITHHELD, "b": [ys.REFERENCE_WITHHELD],
                                 "c": ys.REFERENCE_WITHHELD, "d": ys.REFERENCE_WITHHELD, "e": "plain"})
        self.assertEqual(counter["references_withheld"], 4)
        for canary in (SECRET_CANARY, ENV_CANARY):
            self.assertNotIn(canary, json.dumps(value))


class TruthfulCompletenessTests(YamlGatewayCase):
    """Unavailable is never complete; the rows are at most partial."""

    async def test_successful_reads_are_partial_with_four_fixed_reads(self):
        factory = await self.gateway_reader()
        _, snapshot, _, _ = await build(home(), factory)
        self.assertEqual(self.sent_paths(), ["template", "script", "homeassistant.packages"])
        sources = snapshot.global_sources
        for source_type in YAML_SOURCE_TYPES:
            with self.subTest(source_type=source_type):
                row = sources.coverage_for(source_type)
                self.assertEqual((row.provider, row.completeness), ("upstream_read_gateway", "partial"))
                self.assertIn(ys.LABELED_KEYS_WARNING.format(key=source_type), row.warnings)
                self.assertIn(ys.SCOPE_WARNINGS[source_type], row.warnings)
                self.assertIn("purpose dependency_index", row.policy)

    async def test_homeassistant_is_read_only_to_classify_absent_packages(self):
        documents = {"template": TEMPLATE_YAML, "script": SCRIPT_YAML,
                     "homeassistant": {"name": "Synthetic home"}}
        factory = await self.gateway_reader(documents)
        _, snapshot, _, _ = await build(home(), factory)
        self.assertEqual(self.sent_paths(), ["template", "script", "homeassistant.packages", "homeassistant"])
        self.assertEqual(len(self.audit_events()), 4)
        self.assertEqual(snapshot.global_sources.coverage_for("template").completeness, "partial")

    async def test_no_reader_is_unavailable_never_complete(self):
        _, snapshot, _, _ = await build(home(), None)
        for source_type in YAML_SOURCE_TYPES:
            row = snapshot.global_sources.coverage_for(source_type)
            self.assertEqual((row.provider, row.completeness), ("none", "unavailable"))
            self.assertIn(ys.UNAVAILABLE_WARNINGS["yaml_reader_not_admitted"], row.warnings)

    async def test_factory_returning_none_or_raising_is_unavailable(self):
        def broken():
            raise RuntimeError("synthetic factory failure")
        for factory in (lambda: None, broken):
            with self.subTest(factory=factory):
                _, snapshot, _, _ = await build(home(), factory)
                for source_type in YAML_SOURCE_TYPES:
                    row = snapshot.global_sources.coverage_for(source_type)
                    self.assertEqual(row.completeness, "unavailable")
                    self.assertNotEqual(row.completeness, "complete")

    async def test_oversize_result_is_refused_and_the_row_is_unavailable(self):
        filler = {f"k{i}": "lorem ipsum. " * 125 for i in range(40)}
        documents = {**DOCUMENTS, "template": [{"sensor": [{"name": "x", "state": "{{ 1 }}",
                                                            "attributes": filler}]}]}
        factory = await self.gateway_reader(documents, response_size_limit=20_000)
        _, snapshot, _, _ = await build(home(), factory)
        sources = snapshot.global_sources
        template = sources.coverage_for("template")
        self.assertEqual((template.provider, template.completeness, template.evidence_count),
                         ("none", "unavailable", 0))
        self.assertTrue(any(item.startswith("template read failed:") for item in template.warnings))
        self.assertFalse([item for item in sources.findings if item.source_type == "template"])
        self.assertNotIn("lorem ipsum", repr(sources))
        # The other row keeps its own read.
        self.assertEqual(sources.coverage_for("script").completeness, "partial")

    async def test_truncated_result_is_partial_with_a_counted_gap(self):
        wide = {**SCRIPT_YAML, "synthetic_wide": {"sequence": [{"delay": 1}], "variables": {"v": [0] * 4100}}}
        factory = await self.gateway_reader({**DOCUMENTS, "script": wide})
        _, snapshot, _, _ = await build(home(), factory)
        row = snapshot.global_sources.coverage_for("script")
        self.assertEqual(row.completeness, "partial")
        self.assertIn("script_value_truncated: 1", row.warnings)

    async def test_failed_read_is_unavailable_and_drops_its_findings(self):
        factory = await self.gateway_reader()
        base = self.transport.respond

        def respond(tool, arguments):
            if arguments["yaml_path"] == "script":
                return ok({"success": False, "error": {"code": "synthetic_failure"}})
            return base(tool, arguments)

        self.transport.respond = respond
        _, snapshot, _, _ = await build(home(), factory)
        sources = snapshot.global_sources
        script = sources.coverage_for("script")
        self.assertEqual((script.provider, script.completeness), ("none", "unavailable"))
        self.assertFalse([item for item in sources.findings if item.source_type == "script"])
        self.assertEqual(sources.coverage_for("template").completeness, "partial")

    async def test_authority_lost_mid_phase_is_unavailable_with_no_findings(self):
        factory = await self.gateway_reader()
        real = factory()
        calls = {"n": 0}

        def current():
            calls["n"] += 1
            return calls["n"] < 3

        reader = replace(real, current=current)
        result = await ys.collect_yaml_sources(reader, [], registry_complete=True, secret=SECRET)
        self.assertEqual(result.findings, ())
        for row in (result.template_coverage, result.script_coverage):
            self.assertEqual(row.completeness, "unavailable")
            self.assertIn(ys.UNAVAILABLE_WARNINGS["provider_authority_changed"], row.warnings)

    async def test_retired_reader_withholds_cached_evidence(self):
        factory = await self.gateway_reader()
        live = {"current": True}

        def retiring():
            reader = factory()
            return replace(reader, current=lambda: live["current"] and reader.current())

        _, snapshot, _, _ = await build(home(), retiring)
        self.assertTrue(any(item.source_type == "template" for item in snapshot.global_sources.visible().findings))
        live["current"] = False
        visible = snapshot.global_sources.visible()
        self.assertFalse([item for item in visible.findings if item.source_type in YAML_SOURCE_TYPES])
        for source_type in YAML_SOURCE_TYPES:
            row = visible.coverage_for(source_type)
            self.assertEqual((row.completeness, row.evidence_count), ("unavailable", 0))
        self.assertNotEqual(visible.fingerprint, snapshot.global_sources.fingerprint)
        block = grd.disclose(lambda: snapshot, (TEMPLATE_TARGET,))
        self.assertEqual(block["completeness"]["template"], "unavailable")
        self.assertEqual(block["children"][0]["items"], [])

    async def test_slow_read_times_out_inside_the_phase(self):
        class Slow:
            purpose = ys.READER_PURPOSE
            provenance = "synthetic slow reader"

            def current(self):
                return True

            async def read(self, path):
                await asyncio.sleep(3600)

        with patch.object(ys, "YAML_READ_SECONDS", 0.01):
            result = await ys.collect_yaml_sources(Slow(), [], registry_complete=True, secret=SECRET)
        for row in (result.template_coverage, result.script_coverage):
            self.assertEqual(row.completeness, "unavailable")
            self.assertIn(f"{row.source_type} read failed: timeout", row.warnings)


class UiTemplateHelperTests(YamlGatewayCase):
    """UI template helpers are counted from the registry and never read."""

    async def test_ui_helpers_keep_the_row_partial_and_are_never_read(self):
        factory = await self.gateway_reader()
        index, snapshot, rest, websocket = await build(home(ui_helpers=True), factory)
        template = snapshot.global_sources.coverage_for("template")
        self.assertEqual(template.completeness, "partial")
        self.assertIn(ys.UI_HELPER_WARNING.format(count=1, plural=""), template.warnings)
        self.assertIn("ui_template_helpers_not_read: 1", template.warnings)
        # No config-entry, options-flow or helper configuration request was sent.
        self.assertFalse([kind for kind in websocket.ha.ws_counts
                          if "config_entries" in kind or "options" in kind or "flow" in kind])
        self.assertEqual(self.sent_paths(), ["template", "script", "homeassistant.packages"])
        # A UI helper gets no YAML finding and no registry identity.
        self.assertFalse([item for item in snapshot.global_sources.findings
                          if item.source_entity_id == UI_HELPER])

    async def test_ui_helpers_are_counted_when_no_reader_is_admitted(self):
        _, snapshot, _, _ = await build(home(ui_helpers=True), None)
        template = snapshot.global_sources.coverage_for("template")
        self.assertEqual(template.completeness, "unavailable")
        self.assertIn(ys.UI_HELPER_WARNING.format(count=1, plural=""), template.warnings)


class ConsumerTests(YamlGatewayCase):
    """Template and YAML script references in both analyses and the plan disclosure."""

    async def asyncSetUp(self):
        factory = await self.gateway_reader()
        self.index, self.snapshot, self.rest, self.websocket = await build(home(), factory)

    async def test_template_and_script_references_in_both_analyses(self):
        expected = {
            TEMPLATE_TARGET: ("template", "template[0].sensor[0]", TEMPLATE_ENTITY),
            TRIGGER_TARGET: ("template", "template[1]", None),
            PACKAGE_TARGET: ("template", "packages.synthetic_pkg.template[0].switch[0]", None),
            SCRIPT_TARGET: ("script", "synthetic_yaml_only", None),
            PACKAGE_SCRIPT_TARGET: ("script", "synthetic_pkg_script", None),
        }
        for target, (source_type, source_id, source_entity) in expected.items():
            with self.subTest(target=target):
                dependency, impact = await self.analyses(self.index, self.rest, self.websocket, target)
                self.assertEqual(dependency["overview"]["dependency_status"], "referenced")
                found = {(item["source_type"], item["source_id"]) for item in dependency["findings"]}
                self.assertIn((source_type, source_id), found)
                finding = next(item for item in dependency["findings"]
                               if (item["source_type"], item["source_id"]) == (source_type, source_id))
                self.assertEqual(finding.get("source_entity_id"), source_entity)
                self.assertFalse(dependency["overview"]["coverage_complete"])
                row = coverage_row(dependency["source_coverage"], source_type)
                self.assertEqual(row["completeness"], "partial")
                self.assertIn("upstream_read_gateway", row["provider"])
                affected = {(item["affected_object_type"], item["affected_object_id"])
                            for item in impact["findings"] if item["rule_id"] == "remove_orphaned_consumer"}
                # Impact names the mapped entity, else the positional source.
                self.assertIn((source_type, source_entity or f"{source_type}:{source_id}"), affected)
                self.assertEqual(impact["final_assessment"], "review_required")
                impact_row = coverage_row(impact["source_coverage_matrix"], source_type)
                self.assertEqual(impact_row["completeness"], "partial")
                self.assertEqual(impact_row["provider"], "upstream_read_gateway")

    async def test_source_types_filter_excludes_yaml_references(self):
        output = await EntityDependencyAnalysisService(self.index).analyze(
            entity_id=TEMPLATE_TARGET, detail_level="evidence", source_types=["group"])
        self.assertEqual(output.data["findings"], [])

    async def test_plan_disclosure_lists_template_and_script_references(self):
        block = grd.disclose(lambda: self.snapshot, (TEMPLATE_TARGET,))
        self.assertEqual(block["status"], "read")
        self.assertEqual(block["completeness"]["template"], "partial")
        self.assertEqual(block["completeness"]["script"], "partial")
        self.assertIn("template", block["reasons"])
        self.assertIn("script", block["reasons"])
        (child,) = block["children"]
        self.assertEqual([(item["type"], item["id"]) for item in child["items"]],
                         [("template", "template[0].sensor[0]")])
        (child,) = grd.disclose(lambda: self.snapshot, (SCRIPT_TARGET,))["children"]
        self.assertEqual([(item["type"], item["id"]) for item in child["items"]],
                         [("script", "synthetic_yaml_only")])

    async def test_yaml_evidence_stays_outside_the_governance_bound_snapshot(self):
        self.assertFalse([item for item in self.snapshot.findings
                          if item.source_type == "template"
                          or (item.source_type == "script" and item.source_id == "synthetic_yaml_only")])
        _, unwired, _, _ = await build(home(), None)
        self.assertEqual(unwired.fingerprint, self.snapshot.fingerprint)
        self.assertNotEqual(unwired.global_sources.fingerprint, self.snapshot.global_sources.fingerprint)

    async def test_integrity_template_row_stays_not_supported(self):
        self.assertNotIn("template", integrity_provider.PARTITION_SOURCE_TYPES)
        self.assertNotIn("script", integrity_provider.PARTITION_SOURCE_TYPES)
        self.assertIn("HAMCP-264", integrity_provider.TEMPLATE_NOT_CONSUMED_WARNING)
        self.assertEqual(PARTITION_SOURCE_TYPES[-1], "script")
        # HAMCP-358 (10): the analysis itself, with the reader wired. The
        # partition holds a partial template row with evidence; integrity
        # analysis must report its own not_supported row, never that one.
        own = self.snapshot.global_sources.visible().coverage_for("template")
        self.assertEqual((own.completeness, own.provider), ("partial", "upstream_read_gateway"))
        self.assertGreater(own.evidence_count, 0)
        self.assertTrue(own.warnings)
        service = ConfigurationIntegrityAnalysisService(integrity_provider.DirectHaIntegrityProvider(
            self.index, self.rest, self.websocket, secret=SECRET, ha_token=SECRET))
        output = await service.analyze(source_types=ALL_SOURCES, detail_level="evidence", limit=100)
        row = coverage_row(output.data["source_coverage_matrix"], "template")
        self.assertTrue(row["requested"])
        self.assertEqual(row["completeness"], "not_supported")
        self.assertEqual(row["provider"], "none")
        self.assertEqual(row["warnings"], [integrity_provider.TEMPLATE_NOT_CONSUMED_WARNING])
        self.assertEqual((row["items_examined"], row["failed_items"]), (0, 0))
        self.assertFalse(row["assessment_complete"])
        self.assertFalse([item for item in output.data["findings"] if item.get("source_type") == "template"])


class BoundTests(unittest.IsolatedAsyncioTestCase):
    """Collector bounds make the row partial with a classified terminal."""

    class Reader:
        purpose = ys.READER_PURPOSE
        provenance = "synthetic bounded reader"

        def __init__(self, documents):
            self.documents = documents
            self.reads = []

        def current(self):
            return True

        async def read(self, path):
            self.reads.append(path)
            found = path in self.documents
            return {"success": True, "metadata": {"provider": "upstream_read_gateway", "fallback_occurred": False},
                    "data": {"yaml_path": path, "file": "configuration.yaml", "found": found,
                             "value": self.documents.get(path), "completeness": "complete", "limitations": []}}

    async def collect(self, documents):
        reader = self.Reader(documents)
        result = await ys.collect_yaml_sources(reader, [], registry_complete=True, secret=SECRET)
        return reader, result

    async def test_template_and_script_source_bounds(self):
        sections = [{"sensor": [{"name": f"s{i}", "state": "{{ states('sensor.synthetic_x') }}"}]}
                    for i in range(3)]
        scripts = {f"synthetic_{i}": {"sequence": [{"action": "light.turn_on",
                                                    "target": {"entity_id": "light.synthetic_x"}}]}
                   for i in range(3)}
        with patch.object(ys, "MAX_YAML_TEMPLATE_SOURCES", 2), patch.object(ys, "MAX_YAML_SCRIPT_SOURCES", 2):
            reader, result = await self.collect({"template": sections, "script": scripts,
                                                 "homeassistant.packages": {}})
        self.assertEqual(reader.reads, ["template", "script", "homeassistant.packages"])
        reasons = {item[3] for item in result.terminals}
        self.assertLessEqual({"yaml_template_source_limit_exceeded", "yaml_script_source_limit_exceeded"}, reasons)
        self.assertEqual(result.profile["template_sources"], 2)
        self.assertEqual(len(result.script_ids), 2)
        for row in (result.template_coverage, result.script_coverage):
            self.assertEqual(row.completeness, "partial")

    async def test_finding_bound(self):
        scripts = {f"synthetic_{i}": {"sequence": [{"action": "light.turn_on",
                                                    "target": {"entity_id": f"light.synthetic_{i}"}}]}
                   for i in range(5)}
        with patch.object(ys, "MAX_YAML_FINDINGS", 2):
            _, result = await self.collect({"script": scripts, "homeassistant.packages": {}})
        self.assertLessEqual(len(result.findings), 2)
        self.assertIn("yaml_finding_limit_exceeded", {item[3] for item in result.terminals})
        self.assertIn("yaml_finding_limit_exceeded: 1", result.script_coverage.warnings)

    async def test_package_bound_reads_200_inline_packages_and_reports_the_201st(self):
        """HAMCP-358 (8): the 201st inline package is not read and both rows say so."""

        def packages(count):
            return {f"synthetic_pkg_{i:03d}": {"script": {f"synthetic_pkg_script_{i:03d}": {
                "sequence": [{"action": "light.turn_on",
                              "target": {"entity_id": f"light.synthetic_pkg_{i:03d}"}}]}}}
                    for i in range(count)}

        self.assertEqual(ys.MAX_YAML_PACKAGES, 200)
        warning = "package_limit_exceeded: 1"
        # At the bound every package is read and nothing is reported.
        _, result = await self.collect({"homeassistant.packages": packages(200)})
        self.assertEqual(result.profile["inline_packages"], 200)
        self.assertEqual(len(result.script_ids), 200)
        for row in (result.template_coverage, result.script_coverage):
            self.assertNotIn(warning, row.warnings)
        # One past the bound: packages are taken in name order, whatever order
        # the mapping holds them in, so the last name is the one left out.
        ascending = packages(201)
        descending = dict(reversed(list(ascending.items())))
        # The premise: the mapping reaches the collector in the order it was built.
        self.assertEqual(list(ys.withhold_references(descending, Counter()))[:2],
                         ["synthetic_pkg_200", "synthetic_pkg_199"])
        for order, mapping in (("ascending", ascending), ("descending", descending)):
            with self.subTest(insertion_order=order):
                reader, result = await self.collect({"homeassistant.packages": mapping})
                self.assertEqual(list(reader.documents["homeassistant.packages"]), list(mapping))
                self.assertEqual(result.profile["inline_packages"], 200)
                self.assertEqual(len(result.script_ids), 200)
                self.assertIn("synthetic_pkg_script_000", result.script_ids)
                self.assertIn("synthetic_pkg_script_199", result.script_ids)
                self.assertNotIn("synthetic_pkg_script_200", result.script_ids)
                targets = {item.target_entity_id for item in result.findings}
                self.assertEqual(len(targets), 200)
                self.assertIn("light.synthetic_pkg_000", targets)
                self.assertIn("light.synthetic_pkg_199", targets)
                self.assertNotIn("light.synthetic_pkg_200", targets)
                for row in (result.template_coverage, result.script_coverage):
                    self.assertEqual(row.completeness, "partial")
                    self.assertIn(warning, row.warnings)

    async def test_includes_and_non_inline_packages_are_partial_never_absent(self):
        _, result = await self.collect({"template": "!include templates.yaml",
                                        "script": "!include scripts.yaml",
                                        "homeassistant.packages": "!include_dir_named packages"})
        self.assertIn("template_include_not_followed: 1", result.template_coverage.warnings)
        self.assertIn("script_include_not_followed: 1", result.script_coverage.warnings)
        self.assertIn("packages_unreadable: 1", result.template_coverage.warnings)
        self.assertEqual(result.findings, ())
        for row in (result.template_coverage, result.script_coverage):
            self.assertEqual(row.completeness, "partial")
            self.assertNotIn("templates.yaml", repr(row))
            self.assertNotIn("scripts.yaml", " ".join(row.warnings[2:]))

    def test_bound_reason_codes_are_classified(self):
        table = bound_table()
        for code in ("yaml_template_source_limit_exceeded", "yaml_script_source_limit_exceeded",
                     "yaml_finding_limit_exceeded"):
            self.assertIn(code, json.dumps(table, default=str))


class ApplicationWiringTests(unittest.TestCase):
    """The runtime wires the reader only under ``dependency_index`` with the shared audit."""

    def setUp(self):
        from test_beta_observability import preserve_create_application_state

        preserve_create_application_state(self)

    def _application(self):
        import tempfile

        from ha_mcp_engineering import application
        from test_beta_observability import settings

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        application.create_application(settings(str(Path(directory.name) / "audit.jsonl")))
        return application

    def test_application_wires_dependency_index_reader_with_shared_audit(self):
        application = self._application()
        provider = application.DEPENDENCY_ANALYSIS.require().index.provider
        self.assertIsNotNone(provider.yaml_reader_factory)
        gateway = application.UPSTREAM_READ_GATEWAY
        yaml_calls, script_calls = [], []
        with patch.object(gateway, "yaml_config_reader",
                          side_effect=lambda **kw: yaml_calls.append(kw)), \
                patch.object(gateway, "script_dependency_reader",
                             side_effect=lambda **kw: script_calls.append(kw)):
            provider.yaml_reader_factory()
            provider.script_reader_factory()
        # Exactly one capture, only under the dependency_index purpose, never
        # the recorder_settings default or any other purpose.
        self.assertEqual(len(yaml_calls), 1)
        self.assertEqual(set(yaml_calls[0]), {"audit", "purpose"})
        self.assertEqual(yaml_calls[0]["purpose"], ys.READER_PURPOSE)
        # The same audit sink as the build's other diagnostic readers.
        self.assertIsNotNone(yaml_calls[0]["audit"])
        self.assertIs(yaml_calls[0]["audit"], script_calls[0]["audit"])

    def test_unadmitted_or_unwired_reader_is_yaml_reader_not_admitted(self):
        from ha_mcp_engineering.dependency.script_sources import create_script_reader

        application = self._application()
        provider = application.DEPENDENCY_ANALYSIS.require().index.provider
        wired = provider.yaml_reader_factory
        self.assertIsNotNone(wired)
        # No upstream catalog is admitted in this process: the wired factory
        # yields no reader, and an unwired provider has no factory at all.
        for factory in (wired, None):
            with self.subTest(wired=factory is not None):
                sources = asyncio.run(ys.collect_yaml_sources(
                    create_script_reader(factory), [], registry_complete=True, secret=SECRET,
                ))
                self.assertEqual(sources.profile["read_attempts"], 0)
                for row in (sources.template_coverage, sources.script_coverage):
                    self.assertEqual((row.provider, row.completeness), ("none", "unavailable"))
                    self.assertIn(ys.UNAVAILABLE_WARNINGS["yaml_reader_not_admitted"], row.warnings)


def tearDownModule():
    # HAMCP-288: ApplicationWiringTests calls create_application, which installs
    # the process-wide secret-path redaction; reset it for the next module.
    from ha_mcp_engineering import secret_paths

    secret_paths.reset()


if __name__ == "__main__":
    unittest.main()
