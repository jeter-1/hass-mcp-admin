"""HAMCP-351: exact ha-mcp 9.1.2 admission at 9.1.0 parity (Engineering 2.6.0).

Josh decision, 2026-11-09 (register HAMCP-351): admit ha-mcp 9.1.2 with exact
admission and the same surface as 8.5.0/9.1.0 today (delegated reads, typed
control, dashboard, projections). These tests pin:

* the exact reviewed identity (index, per-platform manifests, image revision,
  source commit, entry id) with 9.1.0 kept and no 9.1.1 row;
* the 9.1.2 policy equals 9.1.0's classifications, so the blocked tools
  (ha_manage_backup, ha_manage_hacs, ha_report_issue, ha_set_integration)
  stay blocked: never read routes, never exposed;
* release-bound adapters: the old ha_get_integration fingerprint (798a5110)
  and the 9.1.0 ha_config_list_helpers fingerprint are refused under 9.1.2,
  and a 9.1.0 adapter token can never satisfy a 9.1.2 policy (or vice versa);
* the new 9.1.2 ``related`` key and its warning are removed by the compiled
  ha_config_get_automation projection;
* end to end (loopback RedirectPeer, real gateway): the 9.1.2 exposed surface
  equals 9.1.0's, and a 9.1.1 peer is refused with no delegated exposure;
* the error-contract adapter, the history parser row and the exact-image lane
  peer selection are version bound.

Loopback peers and committed captures only; every secret is synthetic.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests"), str(ROOT / "scripts")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ha_mcp_engineering.ha_core_readmission.routes import RETIRED_DELEGATED_READS  # noqa: E402
from ha_mcp_engineering.ha_mcp_readmission import ha_mcp as readmission  # noqa: E402
from ha_mcp_engineering.history_slices import contracts as history_contracts  # noqa: E402
from ha_mcp_engineering.history_slices import service as history_service  # noqa: E402
from ha_mcp_engineering.providers import upstream_platform_reads as platform  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_1 as reads_9_1  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_1_config as config_9_1  # noqa: E402
from ha_mcp_engineering.providers import upstream_yaml_reader as yaml_reader  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    USED_TOOL_ADMISSION_RELEASES,
    UpstreamToolPolicyError,
    load_reviewed_upstream_release_registry,
    load_upstream_tool_policy,
)
from support.ha_mcp_redirect_peer import RedirectPeer  # noqa: E402
import test_hamcp319_history as history_fixtures  # noqa: E402
import test_hamcp319_no_redirect as part1  # noqa: E402
import test_hamcp319_no_redirect_useful as useful  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "exact_image_admission_91_typed_hamcp351", ROOT / "scripts/exact_image_admission_91_typed.py")
typed_lane = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = typed_lane
_SPEC.loader.exec_module(typed_lane)

RUNTIME = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
POLICY_910 = RUNTIME / "upstream_tool_policy_9_1_0.json"
POLICY_912 = RUNTIME / "upstream_tool_policy_9_1_2.json"
REGISTRY = load_reviewed_upstream_release_registry()

INDEX_912 = "sha256:381b1e01635d3c51f35a01882863610bc0b76a22bae93835a6a5a5c926d9ecb4"
PLATFORMS_912 = {
    "linux/amd64": "sha256:7ec9a929bfa3aaff1a6ba47e520c6a634064450d39920633438ca4b4ee6fe334",
    "linux/arm64": "sha256:62cc02ab300289727a26f251eaac0cd7f20bf4c133e100be18c5a51f3cf5523c",
}
REVISION_912 = "b35a3d22a97b262e5d8520cc419cf0acdba9cac1"
SOURCE_912 = "bab615dfc90cc510f5e19a9118707d0fae03a37c"
SOURCE_910 = "d2e3f4b0647d815fb32d530fed15a6a99b1d4b33"
INTEGRATION_FP_910 = "798a5110a0e7717c3db87c8ff231103cdba9e887c35001164f850cf13887cacf"
INTEGRATION_FP_912 = "82aec3a9ea8ffa0250d292b40d3c2d7bc0653233320b02d854a02451243bf4f5"
HELPERS = "ha_config_list_helpers"
AUTOMATION = "ha_config_get_automation"
BLOCKED = ("ha_manage_backup", "ha_manage_hacs", "ha_report_issue", "ha_set_integration")
# The exact 9.1.2 wire catalog (standalone capture, filesystem tools on).
WIRE_912 = ROOT / "docs/evidence/hamcp351/catalogs/standalone-fs-on-tools-response.json"
DEADLINE = part1.DEADLINE_SECONDS + 5


def _policy(version):
    return REGISTRY.by_version[version].policy


def _load(path, version, tag, commit):
    return load_upstream_tool_policy(
        path, expected_version=version, expected_source_tag=tag, expected_source_commit=commit)


class RegistryIdentityTests(unittest.TestCase):
    def test_exact_912_identity_and_no_911(self):
        release = REGISTRY.by_version["9.1.2"]
        self.assertEqual(release.entry_id, "ha-mcp-v9.1.2-381b1e01")
        self.assertEqual(release.image_index_digest, INDEX_912)
        self.assertEqual(dict(release.architecture_image_digests_by_platform), PLATFORMS_912)
        self.assertEqual(release.image_revision, REVISION_912)
        self.assertEqual(release.source_commit, SOURCE_912)
        self.assertEqual(release.release_tag, "v9.1.2")
        self.assertFalse(release.revoked)
        self.assertEqual(release.advertised_tool_count, 77)
        self.assertEqual(release.runtime_contract_fingerprint_model, "ha-mcp-operational-tool-descriptor-v2")
        # 9.1.0 stays admitted exactly as HAMCP-319 reviewed it.
        kept = REGISTRY.by_version["9.1.0"]
        self.assertEqual(kept.entry_id, "ha-mcp-v9.1.0-4259f247")
        self.assertFalse(kept.revoked)
        self.assertNotIn("9.1.1", REGISTRY.by_version)
        self.assertEqual(USED_TOOL_ADMISSION_RELEASES, frozenset({"9.1.0", "9.1.2"}))

    def test_provider_dispositions_equal_910(self):
        self.assertEqual(REGISTRY.by_version["9.1.2"].provider_dispositions,
                         REGISTRY.by_version["9.1.0"].provider_dispositions)
        self.assertEqual(yaml_reader.OPTIONAL_TOOL_DISPOSITIONS["9.1.2"], "admitted")


class PolicyParityTests(unittest.TestCase):
    def test_classifications_and_read_routes_equal_910(self):
        old, new = _policy("9.1.0"), _policy("9.1.2")
        self.assertEqual(sorted(old.by_name), sorted(new.by_name))
        for name in old.by_name:
            with self.subTest(tool=name):
                self.assertEqual(new.by_name[name].classification, old.by_name[name].classification)
                self.assertEqual(new.by_name[name].exposed_name, old.by_name[name].exposed_name)
                self.assertEqual(new.by_name[name].is_read_route, old.by_name[name].is_read_route)
        self.assertEqual(new.classification_counts, old.classification_counts)

    def test_blocked_tools_stay_blocked(self):
        for version in ("9.1.0", "9.1.2"):
            for name in BLOCKED:
                with self.subTest(version=version, tool=name):
                    entry = _policy(version).by_name[name]
                    self.assertFalse(entry.is_read_route)
                    self.assertNotEqual(entry.classification, "automatic_read")
                    self.assertEqual(entry.argument_restrictions, ())

    def test_integration_rebinds_and_old_fingerprint_is_refused(self):
        entry = _policy("9.1.2").by_name[platform.INTEGRATION]
        self.assertEqual(entry.input_schema_fingerprint, INTEGRATION_FP_912)
        self.assertEqual(entry.argument_restrictions, ("ha-mcp-9.1.2-platform-integrations-v1",))
        self.assertTrue(platform.is_platform_adapter(entry))
        stale = replace(entry, input_schema_fingerprint=INTEGRATION_FP_910)
        self.assertFalse(platform.is_platform_adapter(stale))
        self.assertFalse(stale.is_read_route)
        # The 9.1.0 binding is untouched.
        self.assertEqual(_policy("9.1.0").by_name[platform.INTEGRATION].input_schema_fingerprint,
                         INTEGRATION_FP_910)

    def test_list_helpers_binds_its_912_fingerprint_only(self):
        entry = _policy("9.1.2").by_name[HELPERS]
        self.assertEqual(reads_9_1.adapter_release(entry), "9.1.2")
        old_fp = reads_9_1.INPUT_FINGERPRINTS[HELPERS]
        self.assertNotEqual(old_fp, reads_9_1.INPUT_FINGERPRINTS_9_1_2[HELPERS])
        self.assertIsNone(reads_9_1.adapter_release(replace(entry, input_schema_fingerprint=old_fp)))
        # The 9.1.0 token on the 9.1.2 fingerprint satisfies neither release.
        crossed = replace(_policy("9.1.0").by_name[HELPERS],
                          input_schema_fingerprint=entry.input_schema_fingerprint)
        self.assertIsNone(reads_9_1.adapter_release(crossed))

    def test_every_912_read_adapter_names_its_own_release(self):
        adapters = [e for e in _policy("9.1.2").tools if reads_9_1.names_adapter(e) is not None]
        self.assertEqual(len(adapters), len(reads_9_1.INPUT_FINGERPRINTS))
        for entry in adapters:
            with self.subTest(tool=entry.upstream_name):
                self.assertEqual(reads_9_1.adapter_release(entry), "9.1.2")
                self.assertEqual(reads_9_1.adapter_token(entry), reads_9_1.ADAPTER_9_1_2)


class PolicyLoaderRefusalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def mutated(self, source, name, **changes):
        value = json.loads(source.read_text(encoding="utf-8"))
        entry = next(item for item in value["tools"] if item["upstream_name"] == name)
        entry.update(changes)
        path = Path(self.tmp.name) / source.name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_committed_policies_load(self):
        self.assertEqual(_load(POLICY_912, "9.1.2", "v9.1.2", SOURCE_912).reviewed_upstream_version, "9.1.2")
        self.assertEqual(_load(POLICY_910, "9.1.0", "v9.1.0", SOURCE_910).reviewed_upstream_version, "9.1.0")

    def test_912_policy_is_refused_under_910_identity(self):
        with self.assertRaises(UpstreamToolPolicyError):
            _load(POLICY_912, "9.1.0", "v9.1.0", SOURCE_910)
        with self.assertRaises(UpstreamToolPolicyError):
            _load(POLICY_910, "9.1.2", "v9.1.2", SOURCE_912)

    def test_old_integration_fingerprint_is_refused(self):
        path = self.mutated(POLICY_912, platform.INTEGRATION, input_schema_fingerprint=INTEGRATION_FP_910)
        with self.assertRaisesRegex(UpstreamToolPolicyError, "^policy_uncompiled_platform_adapter$"):
            _load(path, "9.1.2", "v9.1.2", SOURCE_912)

    def test_910_platform_token_in_912_policy_is_refused(self):
        path = self.mutated(POLICY_912, platform.INTEGRATION,
                            argument_restrictions=["ha-mcp-9.1.0-platform-integrations-v1"],
                            input_schema_fingerprint=INTEGRATION_FP_910)
        with self.assertRaisesRegex(UpstreamToolPolicyError, "^policy_uncompiled_platform_adapter$"):
            _load(path, "9.1.2", "v9.1.2", SOURCE_912)

    def test_cross_release_read_adapter_is_refused(self):
        cases = {
            "910_token": dict(argument_restrictions=[reads_9_1.ADAPTER]),
            "910_helpers_fingerprint": dict(input_schema_fingerprint=reads_9_1.INPUT_FINGERPRINTS[HELPERS]),
        }
        for label, changes in cases.items():
            with self.subTest(case=label):
                path = self.mutated(POLICY_912, HELPERS, **changes)
                with self.assertRaisesRegex(UpstreamToolPolicyError, "^policy_uncompiled_read_adapter$"):
                    _load(path, "9.1.2", "v9.1.2", SOURCE_912)
        path = self.mutated(POLICY_910, HELPERS, argument_restrictions=[reads_9_1.ADAPTER_9_1_2])
        with self.assertRaisesRegex(UpstreamToolPolicyError, "^policy_uncompiled_read_adapter$"):
            _load(path, "9.1.0", "v9.1.0", SOURCE_910)


class AutomationRelatedProjectionTests(unittest.TestCase):
    def payload(self, **extra):
        base = deepcopy(useful.CONFIG_91["structuredContent"])
        base.update(extra)
        return base

    def project(self, payload):
        project, _completeness = config_9_1.PROJECTIONS[AUTOMATION]
        return project(payload, dict(useful.CONFIG_91["arguments"]))

    def test_related_and_its_warning_are_removed(self):
        reference = self.project(self.payload())
        related = {"automation": ["automation.synthetic"], "entity": ["light.synthetic"]}
        cases = {
            "related": self.payload(related=related),
            "related_unavailable": self.payload(warnings=["related unavailable: synthetic failure"]),
            "both": self.payload(related=related, warnings=["related unavailable: x"]),
        }
        for label, payload in cases.items():
            with self.subTest(case=label):
                result = self.project(payload)
                self.assertNotIn("related", result)
                self.assertEqual(result, reference)

    def test_other_warnings_are_kept(self):
        result = self.project(self.payload(related={}, warnings=["kept", "related unavailable: y"]))
        self.assertEqual(result["warnings"], ["kept"])
        self.assertNotIn("related", result)

    def test_malformed_related_fails_closed(self):
        # None is the documented "absent" form and is accepted, then dropped.
        for value in ([], "text", 1, True):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    self.project(self.payload(related=value))


def _settings(url):
    return replace(part1._settings(url), ha_mcp_release_registry_enabled=False)


def _catalog(version):
    # The committed reviewed runtime catalog (filesystem tools off) for both.
    return useful.reviewed_catalog(version)


def _wire_catalog_912():
    return tuple(json.loads(WIRE_912.read_text(encoding="utf-8"))["result"]["tools"])


async def _gateway(version, tools, handler=None, *, call=None):
    peer = RedirectPeer(version=version, form="strict_full", tools=tools, page_size=30,
                        call_handler=handler)
    with peer.serve():
        gateway = UpstreamReadGateway()
        gateway.configure(_settings(peer.url))
        await asyncio.wait_for(gateway.initialize(FastMCP("x")), DEADLINE)
        health = gateway.health_snapshot()
        exposed = frozenset(gateway._registered_tool_registry.snapshot())
        result = None
        if call is not None:
            _telemetry, token = begin_request("synthetic-hamcp351")
            try:
                tool = gateway._registered_tool_registry.snapshot()[call[0]]
                result = json.loads(await asyncio.wait_for(tool.run(deepcopy(call[1])), DEADLINE))
            finally:
                end_request(token)
    return health, exposed, result, peer


def _delegated(version):
    return {e.exposed_name for e in _policy(version).tools if e.is_read_route} - RETIRED_DELEGATED_READS


class GatewaySurfaceTests(unittest.TestCase):
    def test_912_exposed_surface_equals_910(self):
        surfaces = {}
        for version in ("9.1.0", "9.1.2"):
            with self.subTest(version=version):
                health, exposed, _result, peer = asyncio.run(_gateway(version, _catalog(version)))
                self.assertEqual(health["admission_status"], "admitted_exact")
                self.assertEqual(health["upstream_server_version"], version)
                self.assertEqual(health["reviewed_upstream_version"], version)
                self.assertEqual(peer.calls, [])
                self.assertFalse(set(BLOCKED) & exposed)
                surfaces[version] = exposed
        self.assertEqual(surfaces["9.1.2"], surfaces["9.1.0"])
        self.assertLessEqual(_delegated("9.1.2") - {yaml_reader.TOOL_NAME}, surfaces["9.1.2"])

    def test_912_filesystem_capture_adds_only_the_reviewed_yaml_reader(self):
        _health, base, _result, _peer = asyncio.run(_gateway("9.1.2", _catalog("9.1.2")))
        health, wire, _result, peer = asyncio.run(_gateway("9.1.2", _wire_catalog_912()))
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertEqual(wire - base, {yaml_reader.TOOL_NAME})
        self.assertEqual(base - wire, set())
        self.assertFalse(set(BLOCKED) & wire)
        self.assertEqual(peer.calls, [])

    def test_912_automation_read_drops_related(self):
        case = useful.NoRedirectUseful._delegated_read_case(None, "9.1.0")
        structured = dict(deepcopy(case.structured), related={"entity": ["light.synthetic_canary"]})

        def handler(name, arguments):
            self.assertEqual((name, arguments), case.upstream)
            return deepcopy(structured)

        health, _exposed, result, peer = asyncio.run(_gateway(
            "9.1.2", _catalog("9.1.2"), handler, call=(case.public_tool, case.public_arguments)))
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertIs(result.get("success"), True, result)
        self.assertEqual(result["metadata"]["upstream_version"], "9.1.2")
        self.assertEqual(result["metadata"]["fallback"], "none")
        self.assertEqual(result["data"]["config"], case.structured["config"])
        self.assertNotIn("light.synthetic_canary", json.dumps(result))
        self.assertEqual(peer.calls, [case.upstream])

    def test_911_peer_is_refused_with_no_delegated_exposure(self):
        health, exposed, _result, peer = asyncio.run(_gateway("9.1.1", _catalog("9.1.2")))
        self.assertNotEqual(health["admission_status"], "admitted_exact")
        self.assertNotIn("9.1.1", health["reviewed_supported_versions"])
        self.assertFalse(_delegated("9.1.2") & exposed)
        self.assertEqual(peer.calls, [])


class VersionBoundContractTests(unittest.TestCase):
    def test_error_adapter_is_version_bound(self):
        aggregate = REGISTRY.by_version["9.1.2"].error_contract_fingerprint
        self.assertEqual(aggregate, REGISTRY.by_version["9.1.0"].error_contract_fingerprint)
        new = readmission._error_adapter(aggregate, upstream_version="9.1.2")
        old = readmission._error_adapter(aggregate, upstream_version="9.1.0")
        self.assertEqual(new.adapter_id, "ha_mcp_error_contract_9_1_2_v1")
        self.assertEqual(new.upstream_versions, frozenset({"9.1.2"}))
        self.assertEqual(old.adapter_id, "ha_mcp_error_contract_9_1_0_v1")
        # 9.1.1 never receives a 9.1.x release-bound adapter. (The unbound
        # pre-9.1 adapter is unchanged; release admission refuses 9.1.1 first.)
        stray = readmission._error_adapter(aggregate, upstream_version="9.1.1")
        self.assertNotIn(getattr(stray, "adapter_id", None),
                         {"ha_mcp_error_contract_9_1_0_v1", "ha_mcp_error_contract_9_1_2_v1"})
        if stray is not None:
            self.assertIsNone(stray.upstream_versions)

    def test_history_parser_rows_are_release_bound(self):
        self.assertIn("9.1.2", history_contracts.REVIEWED_VERSIONS)
        self.assertNotIn("9.1.1", history_contracts.REVIEWED_VERSIONS)
        self.assertNotIn("9.1.1", history_service._PARSERS)
        good = {"state": "on", "last_changed": "2026-10-19T05:30:00-05:00",
                "last_updated": "2026-10-19T05:30:00-05:00"}
        entity = "sensor.synthetic_temperature"
        envelope = history_fixtures.HistorySlicesRowTests.envelope
        for parser_version, attributed in (("9.1.2", "9.1.2"), ("9.1.0", "9.1.0")):
            parsed = history_service._PARSERS[parser_version](envelope([good], version=attributed), entity, 0)
            self.assertNotEqual(parsed, "representation_invalid")
        for parser_version, attributed in (("9.1.2", "9.1.0"), ("9.1.0", "9.1.2"), ("9.1.2", "9.1.1")):
            with self.subTest(parser=parser_version, attributed=attributed):
                raw = envelope([good], version=attributed)
                self.assertEqual(history_service._PARSERS[parser_version](raw, entity, 0),
                                 "representation_invalid")


class LanePeerSelectionTests(unittest.TestCase):
    def row(self, **changes):
        release = typed_lane.reviewed_release("9.1.2")
        row = {"upstream_version": "9.1.2",
               "upstream_image": "ghcr.io/homeassistant-ai/ha-mcp@" + release.image_index_digest,
               "image_index_digest": release.image_index_digest,
               "architecture_image_digests": dict(release.architecture_image_digests_by_platform),
               "image_revision": release.image_revision, "source_commit": release.source_commit}
        row.update(changes)
        return {"candidate": row}

    def test_pinned_912_row_selects_each_architecture(self):
        for arch in ("amd64", "arm64"):
            with self.subTest(arch=arch):
                peer = typed_lane.select_peer_image(self.row(), arch)
                self.assertEqual(peer["image_index_digest"], INDEX_912)
                self.assertEqual(peer["platform_manifest_digest"], PLATFORMS_912["linux/" + arch])
                self.assertEqual(peer["version"], "9.1.2")

    def test_911_and_unpinned_digests_are_refused(self):
        other = "sha256:" + "1" * 64
        cases = {
            "version_911": (self.row(upstream_version="9.1.1"), "peer_version_not_9_1_2"),
            "version_910": (self.row(upstream_version="9.1.0"), "peer_version_not_9_1_2"),
            "tag_only": (self.row(upstream_image="ghcr.io/homeassistant-ai/ha-mcp:9.1.2"),
                         "peer_image_not_digest_pinned"),
            "other_index": (self.row(upstream_image="ghcr.io/homeassistant-ai/ha-mcp@" + other,
                                     image_index_digest=other), "peer_row_differs_from_reviewed_registry"),
            "other_platform": (self.row(architecture_image_digests={**PLATFORMS_912, "linux/amd64": other}),
                               "peer_row_differs_from_reviewed_registry"),
        }
        for label, (peers, code) in cases.items():
            with self.subTest(case=label):
                with self.assertRaisesRegex(Exception, code):
                    typed_lane.select_peer_image(peers, "amd64")


if __name__ == "__main__":
    unittest.main()
