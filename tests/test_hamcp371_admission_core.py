"""HAMCP-371 S8: exact ha-mcp 9.2.0 admission core (Engineering 2.7.0 line).

These tests pin the S8 admission core:

* the exact reviewed 9.2.0 identity (index, per-platform manifests, image
  revision, source commit, entry id), with its exact provider dispositions
  (backup held), as the only compiled release (HAMCP-437);
* the 9.2.0 dashboard is "admitted" with a reviewed attestation (Q8 ruling;
  quarantined at S8, reviewed at SB): the provider authority admits 9.2.0, and
  a quarantined attestation, a changed setter descriptor or an unreviewed
  version still refuses;
* the separate 9.2.0 read-adapter module: its own token, source commit and
  wire fingerprints, a loader gate that refuses crossed tokens and stale
  fingerprints, and the gateway helper that sends only 9.2.0-token entries to
  it (a retired 9.1.2 entry keeps the 9.1 module);
* ha_get_history on 9.2.0 goes through the S9 module: an offset-free time
  refuses before any upstream call (the time semantics and the closed key set
  are tests/test_hamcp371_history_offsets.py);
* the 9.2.0 error adapter is version bound and records a real-Core capture
  (N31), and the history parser row is release bound;
* the YAML read is admitted on 9.2.0 under the unchanged 9.1 contract, bound
  to the profiles-2 capture (N29; the replay conditions are
  tests/test_hamcp371_real_core_replay.py);
* Q1: the built-in 9.2.0 entry admits with both switches off; with either
  switch on and no signed 9.2.0 row the outcome is asserted and reported.

Loopback peers and committed captures only; every secret is synthetic. The
retired 8.6.0 and 9.1.2 releases that 9.2.0 must not trust are the frozen
copies in tests/support/frozen_foreign_releases.py (byte copies at 0f1adcb5).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ha_mcp_engineering.clients import ha_mcp_endpoint as endpoint  # noqa: E402
from ha_mcp_engineering.clients.mcp import (  # noqa: E402
    DashboardTransportError,
    McpDashboardHandshake,
)
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    INTERNAL_ONLY_DELEGATED_READS,
    RETIRED_DELEGATED_READS,
)
from ha_mcp_engineering.ha_mcp_readmission import ha_mcp as readmission  # noqa: E402
from ha_mcp_engineering.ha_mcp_readmission.registry import (  # noqa: E402
    TRUST_ANCHOR_KEY_ID,
    SignedReleaseRegistry,
)
from ha_mcp_engineering.history_slices import contracts as history_contracts  # noqa: E402
from ha_mcp_engineering.history_slices import service as history_service  # noqa: E402
from ha_mcp_engineering.providers import upstream_dashboard as dashboard_module  # noqa: E402
from ha_mcp_engineering.providers import upstream_read_gateway as gateway_module  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_1 as reads_9_1  # noqa: E402
from ha_mcp_engineering.providers import upstream_reads_9_2 as reads_9_2  # noqa: E402
from ha_mcp_engineering.providers import upstream_yaml_reader as yaml_reader  # noqa: E402
from ha_mcp_engineering.providers.upstream_contracts import load_attestations  # noqa: E402
from ha_mcp_engineering.providers.upstream_dashboard import UpstreamDashboardProvider  # noqa: E402
from ha_mcp_engineering.providers.upstream_read_gateway import UpstreamReadGateway  # noqa: E402
from ha_mcp_engineering.request_context import begin_request, end_request  # noqa: E402
from ha_mcp_engineering.signed_registry.models import RegistryValidationError  # noqa: E402
from ha_mcp_engineering.upstream_tool_policy import (  # noqa: E402
    USED_TOOL_ADMISSION_RELEASES,
    UpstreamToolPolicyError,
    _dashboard_attestation_projection,
    load_reviewed_upstream_release_registry,
    load_upstream_tool_policy,
    schema_fingerprint,
)
from signed_registry_fixtures import NOW, RegistrySigner  # noqa: E402
from support import frozen_foreign_releases as foreign  # noqa: E402
from support.ha_mcp_redirect_peer import RedirectPeer  # noqa: E402
import test_hamcp319_no_redirect as part1  # noqa: E402
import test_hamcp319_no_redirect_useful as useful  # noqa: E402

RUNTIME = ROOT / "hass_mcp_engineering_beta/ha_mcp_engineering"
POLICY_920 = RUNTIME / "upstream_tool_policy_9_2_0.json"
POLICY_912 = foreign.POLICY_912
REGISTRY_FILE = RUNTIME / "upstream_release_registry.json"  # read by sr/p1_catalog_admission.py
REGISTRY = load_reviewed_upstream_release_registry()
FOREIGN = foreign.registry()

INDEX_920 = "sha256:9de5bf6c4216142fdffe862900abe72dac8dfe9621dd0b2b050d132372b676ba"
PLATFORMS_920 = {
    "linux/amd64": "sha256:349a3ec6b2f4fa0797651b2c5d8985ca28d7a1028156eefba8d94fe51683fdb8",
    "linux/arm64": "sha256:5799ff6f0b85f01710b725792e32caf79255934a6e80b8af28373768d42e9a80",
}
REVISION_920 = "4d7148309af7e4d3da95e16ced8bb67d6a43b9c1"
SOURCE_920 = "51e3aac788482a081807591cd5d58d07dc4e540b"
ENTRY_920 = "ha-mcp-v9.2.0-9de5bf6c"
SOURCE_912 = foreign.SOURCE_912
ERROR_AGGREGATE = "4ed5b1b4c0aaad0036b4625206e96739b40fc83ec1c944754fef1ed667a10437"
HELPERS = "ha_config_list_helpers"
HISTORY = "ha_get_history"
BLOCKED = ("ha_manage_backup", "ha_manage_hacs", "ha_report_issue", "ha_set_integration")
DEADLINE = part1.DEADLINE_SECONDS + 5


def _policy(version):
    return REGISTRY.by_version[version].policy


def _foreign_policy(version):
    return FOREIGN.by_version[version].policy


def _load(path, version, tag, commit):
    return load_upstream_tool_policy(
        path, expected_version=version, expected_source_tag=tag, expected_source_commit=commit)


class RegistryIdentityTests(unittest.TestCase):
    def test_exact_920_identity(self):
        release = REGISTRY.by_version["9.2.0"]
        self.assertEqual(release.entry_id, "ha-mcp-v9.2.0-9de5bf6c")
        self.assertEqual(release.image_index_digest, INDEX_920)
        self.assertEqual(dict(release.architecture_image_digests_by_platform), PLATFORMS_920)
        self.assertEqual(release.image_revision, REVISION_920)
        self.assertEqual(release.source_commit, SOURCE_920)
        self.assertEqual(release.release_tag, "v9.2.0")
        self.assertFalse(release.revoked)
        self.assertEqual(release.advertised_tool_count, 77)
        self.assertEqual(release.runtime_contract_fingerprint_model, "ha-mcp-operational-tool-descriptor-v2")
        self.assertEqual(release.error_contract_fingerprint, ERROR_AGGREGATE)
        # HAMCP-437 retired the 9.1 releases; the release set is 9.2.0 alone.
        self.assertEqual(USED_TOOL_ADMISSION_RELEASES, frozenset({"9.2.0"}))

    def test_920_provider_dispositions(self):
        # HAMCP-401 Phase 1 admits 9.2.0 lifecycle; backup stays held until
        # HAMCP-381. HAMCP-437 dropped the comparison with 9.1.2's dispositions.
        candidate = dict(REGISTRY.by_version["9.2.0"].provider_dispositions)
        self.assertEqual(candidate, {
            "backup": "held", "dashboard": "admitted", "lifecycle": "admitted",
            "read_gateway": "admitted"})

    def test_the_registry_holds_only_920(self):
        # HAMCP-437 removed every other release; the old "every other entry
        # unchanged" digest has no subject left.
        self.assertEqual(tuple(item.version for item in REGISTRY.releases), ("9.2.0",))
        self.assertEqual(REGISTRY.default_version, "9.2.0")


class DashboardAdmissionTests(unittest.TestCase):
    """Q8 (07:40, amended 08:05), at SB: the reviewed 9.2.0 attestation admits."""

    @staticmethod
    def _handshake(version="9.2.0", tools=None):
        return McpDashboardHandshake(
            protocol_version="2025-03-26", server_name="ha-mcp", server_version=version,
            tools=tuple(useful.reviewed_catalog(version) if tools is None else tools),
            connection_latency_ms=1.0)

    def test_attestation_is_reviewed_like_912(self):
        # The base is the reviewed 9.1.2 row, frozen at 0f1adcb5.
        release, base = REGISTRY.by_version["9.2.0"], FOREIGN.by_version["9.1.2"]
        self.assertEqual(release.provider_disposition("dashboard"), "admitted")
        self.assertEqual(release.dashboard_attestation_status, "reviewed")
        self.assertEqual(base.dashboard_attestation_status, "reviewed")
        self.assertEqual(release.dashboard_attestation_entry_id, ENTRY_920)
        # The registry fingerprints are those of the built-in attestation entry.
        attestation = {a.upstream_version: a for a in load_attestations()}["9.2.0"]
        self.assertEqual(attestation.entry_id, ENTRY_920)
        self.assertEqual(attestation.source_commit, SOURCE_920)
        self.assertEqual(release.dashboard_attestation_fingerprint,
                         schema_fingerprint(_dashboard_attestation_projection(attestation)))
        self.assertEqual(release.dashboard_compiled_constraints_fingerprint,
                         base.dashboard_compiled_constraints_fingerprint)
        self.assertNotEqual(release.dashboard_attestation_fingerprint,
                            base.dashboard_attestation_fingerprint)

    def test_920_dashboard_provider_authority_admits(self):
        provider = UpstreamDashboardProvider()
        authority = provider._dashboard_provider_authority(self._handshake())
        self.assertIn("evidence_hash", authority)
        self.assertEqual(authority["upstream_version"], "9.2.0")
        self.assertEqual(authority["compatibility_entry"], ENTRY_920)
        self.assertEqual(authority["source_commit"], SOURCE_920)
        self.assertEqual(provider._optional_dashboard_provider_authority(self._handshake()),
                         authority)

    def test_920_quarantined_attestation_still_refuses(self):
        release = REGISTRY.by_version["9.2.0"]
        quarantined = replace(release, dashboard_attestation_status="quarantined")
        registry = replace(REGISTRY, releases=tuple(
            quarantined if item.version == "9.2.0" else item for item in REGISTRY.releases))
        provider = UpstreamDashboardProvider()
        with patch.object(dashboard_module, "load_reviewed_upstream_release_registry",
                          return_value=registry):
            with self.assertRaises(DashboardTransportError) as caught:
                provider._dashboard_provider_authority(self._handshake())
            self.assertIsNone(provider._optional_dashboard_provider_authority(self._handshake()))
        self.assertEqual(caught.exception.category, "upstream_version_mismatch")

    def test_920_changed_setter_descriptor_refuses(self):
        tools = deepcopy(list(useful.reviewed_catalog("9.2.0")))
        setter = next(tool for tool in tools if tool["name"] == "ha_config_set_dashboard")
        setter["description"] += " HAMCP-371 synthetic change."
        provider = UpstreamDashboardProvider()
        with self.assertRaises(DashboardTransportError) as caught:
            provider._dashboard_provider_authority(self._handshake(tools=tools))
        self.assertEqual(caught.exception.category, "reviewed_contract_mismatch")
        self.assertIsNone(provider._optional_dashboard_provider_authority(
            self._handshake(tools=tools)))

    def test_unreviewed_version_refuses(self):
        provider = UpstreamDashboardProvider()
        with self.assertRaises(DashboardTransportError) as caught:
            provider._dashboard_provider_authority(replace(
                self._handshake(), server_version="9.2.1"))
        self.assertEqual(caught.exception.category, "upstream_version_mismatch")


class ReadAdapterModuleTests(unittest.TestCase):
    def test_920_adapter_has_its_own_token_and_release(self):
        self.assertEqual(reads_9_2.VERSION, "9.2.0")
        self.assertEqual(reads_9_2.SOURCE, SOURCE_920)
        self.assertEqual(set(reads_9_2.INPUT_FINGERPRINTS), set(reads_9_1.INPUT_FINGERPRINTS))
        self.assertNotIn(reads_9_2.ADAPTER, reads_9_1._RELEASE_BY_ADAPTER)
        self.assertNotIn("9.2.0", reads_9_1.RELEASES)
        self.assertEqual(reads_9_2.PUBLIC_SCHEMAS, reads_9_1.PUBLIC_SCHEMAS)

    def test_three_reads_rebind_and_twenty_equal_912(self):
        changed = {name for name, fp in reads_9_2.INPUT_FINGERPRINTS.items()
                   if fp != reads_9_1.INPUT_FINGERPRINTS_9_1_2[name]}
        self.assertEqual(changed, {"ha_get_history", "ha_list_services", "ha_search"})

    def test_every_920_read_adapter_names_its_own_release(self):
        adapters = [e for e in _policy("9.2.0").tools if reads_9_2.names_adapter(e) is not None]
        self.assertEqual(len(adapters), len(reads_9_2.INPUT_FINGERPRINTS))
        for entry in adapters:
            with self.subTest(tool=entry.upstream_name):
                self.assertEqual(reads_9_2.adapter_release(entry), "9.2.0")
                self.assertEqual(reads_9_2.adapter_token(entry), reads_9_2.ADAPTER)
                self.assertIsNone(reads_9_1.names_adapter(entry))
        self.assertFalse([e for e in _policy("9.2.0").tools if reads_9_1.names_adapter(e)])

    def test_stale_fingerprint_and_crossed_token_satisfy_neither_release(self):
        entry = _policy("9.2.0").by_name["ha_search"]
        stale = replace(entry, input_schema_fingerprint=reads_9_1.INPUT_FINGERPRINTS_9_1_2["ha_search"])
        self.assertIsNone(reads_9_2.adapter_release(stale))
        crossed = replace(_foreign_policy("9.1.2").by_name["ha_search"],
                          input_schema_fingerprint=entry.input_schema_fingerprint)
        self.assertIsNone(reads_9_1.adapter_release(crossed))
        both = replace(entry, argument_restrictions=(reads_9_2.ADAPTER, reads_9_1.ADAPTER_9_1_2))
        self.assertIsNone(reads_9_2.adapter_release(both))

    def test_blocked_tools_stay_blocked(self):
        for name in BLOCKED:
            with self.subTest(tool=name):
                entry = _policy("9.2.0").by_name[name]
                self.assertFalse(entry.is_read_route)
                self.assertNotEqual(entry.classification, "automatic_read")
                self.assertEqual(entry.argument_restrictions, ())

    def test_history_uses_the_s9_module(self):
        from ha_mcp_engineering.providers import upstream_history_9_2 as history_9_2

        entry = _policy("9.2.0").by_name[HISTORY]
        self.assertTrue(reads_9_2.is_adapter(entry))
        arguments = {"entity_ids": "sensor.synthetic_temperature",
                     "start_time": "2026-11-01T00:00:00+00:00"}
        self.assertEqual(reads_9_2.read_arguments(entry, arguments),
                         history_9_2.dispatch_arguments(arguments))
        with self.assertRaises(history_9_2.HistoryTimeOffsetRequired):
            reads_9_2.read_arguments(entry, {**arguments, "start_time": "2026-11-01T00:00:00"})
        with self.assertRaises(ValueError):
            reads_9_2.project_result(entry, {}, {})


class GatewayHelperTests(unittest.TestCase):
    def test_helper_selects_the_module_by_the_entry_release(self):
        # 9.2.0 is shipped; 9.1.2 is the frozen retired release (HAMCP-437
        # dropped the 9.1.0 loop: no reviewed 9.1.0 release is left).
        for version, policy, module in (("9.1.2", _foreign_policy("9.1.2"), reads_9_1),
                                        ("9.2.0", _policy("9.2.0"), reads_9_2)):
            for entry in policy.tools:
                with self.subTest(version=version, tool=entry.upstream_name):
                    expected = (module if (reads_9_1.names_adapter(entry) or reads_9_2.names_adapter(entry))
                                else reads_9_1)
                    self.assertIs(gateway_module._reads(entry), expected)

    def test_helper_keeps_the_91_pass_through_for_other_releases(self):
        # The frozen 8.6.0 release names no 9.x adapter (HAMCP-437 dropped the
        # 8.5.0 and 7.14.1 loops with those releases).
        for version in ("8.6.0",):
            for entry in _foreign_policy(version).tools:
                with self.subTest(version=version, tool=entry.upstream_name):
                    self.assertIs(gateway_module._reads(entry), reads_9_1)

    def test_helper_refuses_an_entry_naming_both_releases(self):
        entry = _policy("9.2.0").by_name["ha_search"]
        both = replace(entry, argument_restrictions=(reads_9_2.ADAPTER, reads_9_1.ADAPTER_9_1_2))
        with self.assertRaisesRegex(ValueError, "^read_adapter_release_ambiguous$"):
            gateway_module._reads(both)


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

    def test_committed_policy_loads(self):
        self.assertEqual(_load(POLICY_920, "9.2.0", "v9.2.0", SOURCE_920).reviewed_upstream_version, "9.2.0")

    def test_920_policy_is_refused_under_912_identity(self):
        # The frozen 9.1.2 policy loads under its own identity, so each refusal
        # is the identity check (the reason code base 0f1adcb5 gave), not an
        # unreadable file.
        self.assertEqual(_load(POLICY_912, "9.1.2", "v9.1.2", SOURCE_912).reviewed_upstream_version, "9.1.2")
        with self.assertRaisesRegex(UpstreamToolPolicyError, "^policy_version_invalid$"):
            _load(POLICY_920, "9.1.2", "v9.1.2", SOURCE_912)
        with self.assertRaisesRegex(UpstreamToolPolicyError, "^policy_version_invalid$"):
            _load(POLICY_912, "9.2.0", "v9.2.0", SOURCE_920)

    def test_crossed_tokens_and_stale_fingerprints_are_refused(self):
        cases = {
            "912_token": dict(argument_restrictions=[reads_9_1.ADAPTER_9_1_2]),
            "910_token": dict(argument_restrictions=[reads_9_1.ADAPTER]),
            "both_tokens": dict(argument_restrictions=[reads_9_2.ADAPTER, reads_9_1.ADAPTER_9_1_2]),
            "912_search_fingerprint": dict(
                input_schema_fingerprint=reads_9_1.INPUT_FINGERPRINTS_9_1_2["ha_search"]),
        }
        for label, changes in cases.items():
            with self.subTest(case=label):
                path = self.mutated(POLICY_920, "ha_search", **changes)
                with self.assertRaisesRegex(UpstreamToolPolicyError, "^policy_uncompiled_read_adapter$"):
                    _load(path, "9.2.0", "v9.2.0", SOURCE_920)
        path = self.mutated(POLICY_912, HELPERS, argument_restrictions=[reads_9_2.ADAPTER])
        with self.assertRaisesRegex(UpstreamToolPolicyError, "^policy_uncompiled_read_adapter$"):
            _load(path, "9.1.2", "v9.1.2", SOURCE_912)


class VersionBoundContractTests(unittest.TestCase):
    def test_error_adapter_is_version_bound_and_real_core(self):
        adapter = readmission._error_adapter(ERROR_AGGREGATE, upstream_version="9.2.0")
        self.assertEqual(adapter.adapter_id, "ha_mcp_error_contract_9_2_0_v1")
        self.assertEqual(adapter.upstream_versions, frozenset({"9.2.0"}))
        self.assertEqual(adapter.evidence_class, "real_core_capture")
        self.assertEqual(readmission._error_adapter(ERROR_AGGREGATE, upstream_version="9.1.2").adapter_id,
                         "ha_mcp_error_contract_9_1_2_v1")
        # Never attached by analogy: 9.2.0 never receives a 9.1.x adapter, and
        # an unreviewed 9.2.x never receives the 9.2.0 adapter.
        for version in ("9.2.1", "9.1.9"):
            stray = readmission._error_adapter(ERROR_AGGREGATE, upstream_version=version)
            self.assertNotEqual(getattr(stray, "adapter_id", None), "ha_mcp_error_contract_9_2_0_v1")
            if stray is not None:
                self.assertIsNone(stray.upstream_versions)
        probes = {(p.probe_id, p.capability_id, p.structured_code, p.shape_fingerprint) for p in adapter.probes}
        expected = readmission._error_adapter(ERROR_AGGREGATE, upstream_version="9.1.2").probes
        self.assertEqual(probes, {(p.probe_id, p.capability_id, p.structured_code, p.shape_fingerprint)
                                  for p in expected})

    def test_history_slices_are_not_bound_to_an_upstream_release(self):
        # HAMCP-511: get_history_slices reads Core natively; no ha-mcp
        # release, parser row or gateway reader admits it.
        self.assertEqual(history_contracts.REVIEWED_VERSIONS, frozenset({history_contracts.NATIVE_VERSION}))
        self.assertNotIn("9.2.0", history_contracts.REVIEWED_VERSIONS)
        self.assertFalse(hasattr(history_service, "_PARSERS"))

    def test_strict_full_selector_binds_920(self):
        self.assertIn("9.2.0", endpoint.STRICT_FULL_SELECTOR_RELEASES)
        self.assertNotIn("9.2.1", endpoint.STRICT_FULL_SELECTOR_RELEASES)


class YamlReadBindingTests(unittest.TestCase):
    """N29: admitted on 9.2.0 under the unchanged 9.1 read contract."""

    def test_920_rows_bind_the_profiles2_capture(self):
        self.assertEqual(yaml_reader.OPTIONAL_TOOL_DISPOSITIONS["9.2.0"], "admitted")
        commit, capture, digest = yaml_reader._CAPTURES["9.2.0"]
        self.assertEqual(commit, SOURCE_920)
        path = ROOT / capture
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        # The unchanged 9.1 contract object (9.1.2 used the same one at 0f1adcb5).
        self.assertIs(yaml_reader.YAML_READ_CONTRACTS["9.2.0"], yaml_reader._CONTRACT_9_1)

    def test_siblings_stay_prohibited(self):
        # The same four siblings 9.1.2 prohibited at 0f1adcb5.
        self.assertEqual(set(yaml_reader._SIBLING_TOOLS_BY_VERSION["9.2.0"]),
                         {"ha_delete_file", "ha_list_files", "ha_read_file", "ha_write_file"})
        delegated = {e.exposed_name for e in _policy("9.2.0").tools if e.is_read_route}
        self.assertFalse(set(yaml_reader._SIBLING_TOOLS_BY_VERSION["9.2.0"]) & delegated)


def _settings(url, **changes):
    return replace(part1._settings(url), **{"ha_mcp_release_registry_enabled": False, **changes})


async def _gateway(version, tools, handler=None, *, call=None, settings=None, signed=None):
    peer = RedirectPeer(version=version, form="strict_full", tools=tools, page_size=30,
                        call_handler=handler)
    with peer.serve():
        gateway = UpstreamReadGateway()
        gateway.configure((settings or _settings)(peer.url), signed_release_registry=signed)
        try:
            await asyncio.wait_for(gateway.initialize(FastMCP("x")), DEADLINE)
            health = gateway.health_snapshot()
            exposed = frozenset(gateway._registered_tool_registry.snapshot())
            # HAMCP-501 W6: the internal-only routes, recorded for the caller.
            peer.internal_routes = frozenset(gateway._internal)
            result = None
            if call is not None:
                _telemetry, token = begin_request("synthetic-hamcp371")
                try:
                    tool = gateway._registered_tool_registry.snapshot()[call[0]]
                    result = json.loads(await asyncio.wait_for(tool.run(deepcopy(call[1])), DEADLINE))
                finally:
                    end_request(token)
        finally:
            # The signed-registry path keeps a persistent session open.
            await asyncio.wait_for(gateway._transport.aclose(), DEADLINE)
    return health, exposed, result, peer


def _delegated(version):
    # HAMCP-501 W6 (G1, as relayed by the architect): the delegated ha_search is
    # admitted internal-only for the native search page readers, never published.
    return ({e.exposed_name for e in _policy(version).tools if e.is_read_route}
            - RETIRED_DELEGATED_READS - INTERNAL_ONLY_DELEGATED_READS)


class GatewaySurfaceTests(unittest.TestCase):
    def test_920_exposed_surface(self):
        # HAMCP-437 dropped the equality with the 9.1.2 surface: 9.1.2 is no
        # longer admitted, so there is no second exposed surface to compare.
        health, exposed, _result, peer = asyncio.run(_gateway("9.2.0", useful.reviewed_catalog("9.2.0")))
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertEqual(health["upstream_server_version"], "9.2.0")
        self.assertEqual(health["reviewed_upstream_version"], "9.2.0")
        self.assertEqual(peer.calls, [])
        self.assertFalse(set(BLOCKED) & exposed)
        self.assertLessEqual(_delegated("9.2.0") - {yaml_reader.TOOL_NAME}, exposed)
        self.assertFalse(INTERNAL_ONLY_DELEGATED_READS & exposed)
        self.assertEqual(peer.internal_routes, INTERNAL_ONLY_DELEGATED_READS)

    def test_920_delegated_read_dispatches_through_the_92_module(self):
        case = useful.NoRedirectUseful._delegated_read_case(None, "9.2.0")

        def handler(name, arguments):
            self.assertEqual((name, arguments), case.upstream)
            return deepcopy(case.structured)

        health, _exposed, result, peer = asyncio.run(_gateway(
            "9.2.0", useful.reviewed_catalog("9.2.0"), handler,
            call=(case.public_tool, case.public_arguments)))
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertIs(result.get("success"), True, result)
        self.assertEqual(result["metadata"]["upstream_version"], "9.2.0")
        self.assertEqual(result["metadata"]["fallback"], "none")
        self.assertEqual(result["data"]["config"], case.structured["config"])
        self.assertEqual(peer.calls, [case.upstream])

    def test_920_offset_free_history_refuses_with_no_upstream_call(self):
        def handler(name, arguments):  # pragma: no cover - must never run
            raise AssertionError(f"unexpected upstream call {name}")

        _health, _exposed, result, peer = asyncio.run(_gateway(
            "9.2.0", useful.reviewed_catalog("9.2.0"), handler,
            call=(HISTORY, {"entity_ids": "sensor.synthetic_temperature",
                            "start_time": "2026-11-01T00:00:00"})))
        self.assertIs(result.get("success"), False, result)
        self.assertEqual(result["error_code"], "invalid_request")
        self.assertEqual(result["details"]["failure_category"], "history_time_offset_required")
        self.assertEqual(peer.calls, [])

    def test_921_peer_is_refused_with_no_delegated_exposure(self):
        health, exposed, _result, peer = asyncio.run(_gateway("9.2.1", useful.reviewed_catalog("9.2.0")))
        self.assertNotEqual(health["admission_status"], "admitted_exact")
        self.assertNotIn("9.2.1", health["reviewed_supported_versions"])
        self.assertFalse(_delegated("9.2.0") & exposed)
        self.assertEqual(peer.calls, [])


class SignedRegistrySwitchTests(unittest.TestCase):
    """Q1 (amended): built-in 9.2.0 admission against each switch, no signed row.

    The positive path (both switches off) must pass. With either switch on and
    no signed 9.2.0 row the outcome is designed behaviour, asserted here and
    reported in docs/evidence/hamcp371/s8/registry/README.md.
    """

    OFF = {"ha_mcp_release_registry_enabled": False, "upstream_trust_registry_enabled": False}

    def admission(self, signed=None, **switches):
        settings = lambda url: _settings(url, **{**self.OFF, **switches})  # noqa: E731
        health, exposed, _result, peer = asyncio.run(_gateway(
            "9.2.0", useful.reviewed_catalog("9.2.0"), settings=settings, signed=signed))
        self.assertEqual(peer.calls, [])
        return health, exposed

    def test_positive_path_with_both_switches_off(self):
        health, exposed = self.admission()
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertEqual(health["reviewed_upstream_version"], "9.2.0")
        self.assertEqual(health["compatibility_registry_status"], "compiled_reviewed_release_registry")
        self.assertLessEqual(_delegated("9.2.0") - {yaml_reader.TOOL_NAME}, exposed)

    def test_release_registry_on_with_a_current_journal_and_no_920_row(self):
        # Designed: the compiled exact entry is the authority; a current signed
        # journal without a 9.2.0 row neither admits nor withdraws it.
        signer = RegistrySigner(key_id=TRUST_ANCHOR_KEY_ID)
        journal = signer.journal_raw(sequence=1, entries=[], revocations=[])

        async def fetcher(_url, _maximum):
            return journal

        with tempfile.TemporaryDirectory() as directory:
            signed = SignedReleaseRegistry(
                enabled=True, public_key=signer.public_key_base64,
                cache_path=Path(directory) / "cache.json", fetcher=fetcher, now=lambda: NOW)
            health, exposed = self.admission(signed, ha_mcp_release_registry_enabled=True)
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertEqual(health["readmission_authority_source"], "compiled_exact")
        registry = health["automatic_readmission_registry"]
        self.assertEqual((registry["enabled"], registry["sequence"], registry["freshness_status"]),
                         (True, 1, "current"))
        self.assertLessEqual(_delegated("9.2.0") - {yaml_reader.TOOL_NAME}, exposed)

    def test_release_registry_on_without_a_trust_anchor_fails_closed_at_configure(self):
        # Designed: an enabled registry with no configured anchor is refused
        # before any upstream connection.
        gateway = UpstreamReadGateway()
        with self.assertRaisesRegex(RegistryValidationError, "^registry_trust_anchor_invalid$"):
            gateway.configure(_settings("http://127.0.0.1:9/synthetic-path-credential/mcp", ha_mcp_release_registry_enabled=True))

    def test_upstream_trust_registry_on_without_a_signed_920_row(self):
        # Designed: the read gateway's 9.2.0 admission does not consult the
        # upstream trust registry (it gates the dashboard provider only).
        health, exposed = self.admission(upstream_trust_registry_enabled=True)
        self.assertEqual(health["admission_status"], "admitted_exact")
        self.assertEqual(health["reviewed_upstream_version"], "9.2.0")
        self.assertLessEqual(_delegated("9.2.0") - {yaml_reader.TOOL_NAME}, exposed)


if __name__ == "__main__":
    unittest.main()
