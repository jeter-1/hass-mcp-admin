"""Ephemeral signed applicability and unchanged historical Core contracts."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from test_integration_inspection_contract import FIXTURES, TARGET, SyntheticClient, c
from core_registry_fixtures import CoreSigner, ProjectedCoreSource, core_entry
from signed_registry_fixtures import NOW
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry
from ha_mcp_engineering.ha_core_readmission.models import CoreAuthorityStatus
from ha_mcp_engineering.ha_core_readmission.profiles import (
    CORE_CAPABILITY_PROFILES, CORE_TYPED_OPERATION_PROFILES, CORE_RUNTIME_CAPABILITY_PROFILES,
    CORE_INTEGRATION_INSPECTION_PROFILES, compiled_exact_authority,
)
from ha_mcp_engineering.ha_core_readmission.probe_profiles import CHILD_DEVICE_PROBE_PROFILE
from ha_mcp_engineering.ha_core_readmission.source import capability_evidence_for_probes
from ha_mcp_engineering.ha_core_readmission import stable_observation, CoreReadmissionCoordinator


class CoreAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_runtime_lease_public_success_and_missing_authority_no_send(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from ha_mcp_engineering.ha_core_readmission import CoreRuntime
        from ha_mcp_engineering.integration_inspection.provider import AlarmoProvider
        from ha_mcp_engineering.integration_inspection.service import IntegrationInspectionService
        from ha_mcp_engineering.integration_inspection.runtime import INTEGRATION_INSPECTION
        from ha_mcp_engineering.tools.integration_inspection import registered_tool
        from ha_mcp_engineering.request_context import begin_request, end_request
        class Source(ProjectedCoreSource):
            async def capture_core_snapshot(self):
                raw = await super().capture_core_snapshot()
                p = CORE_INTEGRATION_INSPECTION_PROFILES[0]
                raw["capability_evidence"].append({"capability_id": p.capability_id,
                    "passed_checks": list(p.required_checks), "semantic_fingerprint": p.contract_fingerprint})
                return raw
        for admitted in (False, True):
            registry, _ = await self.signed(new=admitted)
            source = Source(registry, "2026.9.3", typed_operations=True)
            runtime = CoreRuntime()
            runtime.configure(SimpleNamespace(), source=source, release_registry=registry)
            await runtime.reconcile_once("synthetic-inspector")
            authority = runtime.acquire(c.CORE_REQUIREMENTS)
            self.assertEqual(authority is not None, admitted)
            commits = runtime.consume(authority) if authority else None
            client = SyntheticClient()
            service = IntegrationInspectionService(AlarmoProvider(client, runtime))
            telemetry, token = begin_request()
            telemetry.caller_id = "synthetic_signed_inspector"
            telemetry.core_dispatch_authorizer = lambda: bool(authority and runtime.revalidate(authority, commits))
            try:
                with patch.object(INTEGRATION_INSPECTION, "service", service):
                    report = json.loads(await registered_tool().run({"alarm_entity_id": TARGET}))
                self.assertEqual(report["success"], admitted, report)
                self.assertEqual(len(client.calls), 9 if admitted else 0)
                if admitted:
                    self.assertEqual(report["data"]["membership"]["total_in_scope"], 4)
            finally:
                end_request(token)
                if commits:
                    self.assertTrue(runtime.finish(commits))
            health = runtime.health_snapshot()
            self.assertEqual(health["issued_lease_count"], 0)
            self.assertEqual(health["active_commit_count"], 0)

    async def signed(self, *, new=True, changed=False):
        signer = CoreSigner()
        entry = core_entry("2026.9.3", typed_operations=True)
        if new:
            profile = CORE_INTEGRATION_INSPECTION_PROFILES[0]
            reference = {key: profile.to_mapping()[key] for key in (
                "capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")}
            if changed:
                reference["contract_fingerprint"] = "sha256:" + "e" * 64
            entry["capabilities"].append(reference)
        raw = signer.journal_raw(envelopes=[signer.raw(entries=[entry])])
        async def fetch(_url, _maximum):
            return raw
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        registry = CoreReleaseRegistry(enabled=True, public_key=signer.public_key_base64,
            cache_path=Path(directory.name) / "synthetic-core.json", fetcher=fetch, now=lambda: NOW)
        self.assertTrue(await registry.refresh())
        source = ProjectedCoreSource(registry, "2026.9.3", typed_operations=True)
        captured = await source.capture_core_snapshot()
        profile = CORE_INTEGRATION_INSPECTION_PROFILES[0]
        captured["capability_evidence"].append({"capability_id": profile.capability_id,
            "passed_checks": list(profile.required_checks), "semantic_fingerprint": profile.contract_fingerprint})
        observation = stable_observation(captured, deepcopy(captured))
        return registry, observation

    async def test_positive_signed_exact_reference_and_old_authority_missing_new(self):
        for new, expected in ((False, 19), (True, 20)):
            registry, observation = await self.signed(new=new)
            result = CoreReadmissionCoordinator(CORE_RUNTIME_CAPABILITY_PROFILES).reconcile(observation, registry.selections("2026.9.3"))
            self.assertEqual(sum(d.disposition.admitted for d in result.generation.decisions), expected)
            self.assertEqual(result.generation.decision_for(c.CORE_CAPABILITY).disposition.admitted, new)
            self.assertTrue(result.generation.decision_for("core.basic_websocket_read").disposition.admitted)

    async def test_mismatched_reference_and_per_capability_denial_preserve_siblings(self):
        registry, observation = await self.signed(changed=True)
        coordinator = CoreReadmissionCoordinator(CORE_RUNTIME_CAPABILITY_PROFILES)
        result = coordinator.reconcile(observation, registry.selections("2026.9.3"))
        self.assertFalse(result.generation.decision_for(c.CORE_CAPABILITY).disposition.admitted)
        self.assertEqual(sum(d.disposition.admitted for d in result.generation.decisions), 19)
        registry, observation = await self.signed()
        selections = tuple(replace(s, status=CoreAuthorityStatus.REVOKED) if c.CORE_CAPABILITY in s.capability_ids else s
                           for s in registry.selections("2026.9.3"))
        # Synthetic coordinator-level per-capability denial, not a claim that
        # the current production journal preparer can revoke one reference.
        result = coordinator.reconcile(observation, selections)
        self.assertFalse(result.generation.decision_for(c.CORE_CAPABILITY).disposition.admitted)
        self.assertEqual(sum(d.disposition.admitted for d in result.generation.decisions), 19)

    def test_historical_19_profiles_and_probe_fingerprint_unchanged(self):
        retained = json.loads((FIXTURES / "core_metadata_applicability.json").read_text())
        self.assertEqual([p.to_mapping() for p in CORE_CAPABILITY_PROFILES + CORE_TYPED_OPERATION_PROFILES], retained["old_core_profiles"])
        self.assertEqual(CHILD_DEVICE_PROBE_PROFILE.contract_fingerprint, retained["old_probe_fingerprint"])
        self.assertEqual(len(CORE_RUNTIME_CAPABILITY_PROFILES), 20)
        self.assertNotIn(c.CORE_CAPABILITY, {name for s in compiled_exact_authority("2026.9.1") for name in s.capability_ids})
        profile = CORE_INTEGRATION_INSPECTION_PROFILES[0]
        self.assertTrue(profile.capability_class.semantic)
        self.assertFalse(profile.capability_class.mutation_capable)

    def test_opt_in_projection_requires_known_probe_and_transport(self):
        args = dict(version="2026.9.3", rest_config={"version": "2026.9.3"}, states=[], services=[],
                    websocket_config={"version": "2026.9.3"}, websocket_results={}, probe_profile=CHILD_DEVICE_PROBE_PROFILE)
        old = capability_evidence_for_probes(**args)
        added = capability_evidence_for_probes(**args, include_integration_inspection=True)
        self.assertEqual([x for x in added if x["capability_id"] != c.CORE_CAPABILITY], old)
        self.assertEqual(len(added), len(old) + 1)
        for change in ({"websocket_config": {}}, {"probe_profile": None}):
            observed = capability_evidence_for_probes(**{**args, **change}, include_integration_inspection=True)
            self.assertNotIn(c.CORE_CAPABILITY, {item["capability_id"] for item in observed})
