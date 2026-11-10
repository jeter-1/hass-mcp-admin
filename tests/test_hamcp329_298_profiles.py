"""HAMCP-329/298 dormant created-object Core profiles: design §5 proving tests.

``core.scene_create_delete_operation`` and ``core.created_helper_delete_operation``
are runtime-only, mutation-capable profiles.  They are dormant until the
combined 2.6 signing (design PR #395, seq 18) references them in the
2026.11.0 entry.  These tests prove the additive-profile claims of
``docs/HAMCP_329_298_SCENE_CREATE_AND_CREATE_ROLLBACK_DESIGN.md`` §5:

1. every existing runtime profile keeps its exact reference, compared with
   the retained owner-signed references for every carried Core release;
2. an extended registry entry (the two new references, plus an unknown
   future reference) keeps every existing admission;
3. the shipped reference set admits every existing capability exactly as
   before, and only the new identities stay withheld (pending admission,
   zero Home Assistant requests);
4. older Core releases never admit the new profiles from the extended
   registry, which extends only the 2026.11.0 entry;
5. probe fingerprints are unchanged.

Signed admission uses the test-only ephemeral ``CoreSigner`` key only.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests"), str(ROOT)]

from core_registry_fixtures import CoreSigner, core_entry, NOW  # noqa: E402
from support.core_gate import reconcile_through_gate  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import profiles as core_profiles  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.models import CORE_IDENTITY, CoreCapabilityClass  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.probe_profiles import (  # noqa: E402
    CHILD_DEVICE_NAME_PARTS_PROBE_PROFILE as OCTOBER,
    known_probe_profile,
)
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_CREATED_OBJECT_PROFILES,
    CORE_RUNTIME_CAPABILITY_PROFILES,
)
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    CONFIGURATION_DELETE_PROFILE,
    CREATED_HELPER_DELETE_PROFILE,
    F3_ENTRY_ADMISSION_PROFILES,
    SCENE_CONFIGURATION_PROFILE,
    SCENE_CONFIGURATION_TOOL,
    SCENE_CREATE_DELETE_PROFILE,
    f3_requirements,
    static_tool_requirements,
)
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.source import capability_evidence_for_probes  # noqa: E402

REFERENCE_KEYS = ("capability_id", "profile_id", "profile_version", "adapter_id", "contract_fingerprint")
NEW = (SCENE_CREATE_DELETE_PROFILE, CREATED_HELPER_DELETE_PROFILE)
SIGNED_REGISTRY = ROOT / "upstream-trust" / "ha-core-release-registry.json"
TARGET_CORE = "2026.11.0"
OLDER_CORES = ("2026.10.0", "2026.10.1", "2026.10.3")
# Computed from this branch's compiled contracts.  A change to either
# contract text moves its fingerprint and must be re-reported for signing.
PINNED = {
    SCENE_CREATE_DELETE_PROFILE: (
        "core_scene_create_delete_operation_v1",
        "compiled-core-scene-create-delete-operation-v1",
        "sha256:93378ae0385528f16deb977a985b778ba2dd2d39be6f5cb397ef74f2060a400d",
    ),
    CREATED_HELPER_DELETE_PROFILE: (
        "core_created_helper_delete_operation_v1",
        "compiled-core-created-helper-delete-operation-v1",
        "sha256:3f2e681310a009a4fc853af6fa24f8eca1c671c7dd75bddbbc8b403fec9cdbf4",
    ),
}
# sha256 of the canonical JSON of the 33 runtime references that existed at
# the release/2.6.0 base 8e19b401, in tuple order.  Pinned, not derived.
EXISTING_REFERENCES_SHA256 = "65f3a135d737894506d4c7ac4accbaa90f12865750a037bfcfeba4bd7055f66d"


def reference(profile) -> dict:
    return {key: profile.to_mapping()[key] for key in REFERENCE_KEYS}


def existing_profiles() -> tuple:
    return tuple(p for p in CORE_RUNTIME_CAPABILITY_PROFILES if p.capability_id not in NEW)


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signed_tip_entries() -> list[dict]:
    registry = json.loads(SIGNED_REGISTRY.read_text(encoding="utf-8"))
    tip = max(registry["envelopes"], key=lambda item: item["sequence"])
    return tip["entries"]


class ProfileContractTests(unittest.TestCase):
    def test_new_profiles_are_runtime_only_mutation_capable_and_pinned(self):
        self.assertEqual(tuple(p.capability_id for p in CORE_CREATED_OBJECT_PROFILES), NEW)
        compiled = {p.capability_id for p in CORE_CAPABILITY_PROFILES}
        for profile in CORE_CREATED_OBJECT_PROFILES:
            with self.subTest(capability=profile.capability_id):
                self.assertIn(profile, CORE_RUNTIME_CAPABILITY_PROFILES)
                self.assertNotIn(profile.capability_id, compiled)
                self.assertTrue(profile.capability_class.mutation_capable)
                self.assertTrue(profile.capability_class.semantic)
                self.assertEqual(profile.profile_version, 1)
                self.assertEqual(
                    (profile.profile_id, profile.adapter_id, profile.contract_fingerprint),
                    PINNED[profile.capability_id],
                )
        self.assertIs(
            CORE_CREATED_OBJECT_PROFILES[0].capability_class,
            CoreCapabilityClass.SCENE_CREATE_DELETE_OPERATION,
        )
        self.assertIs(
            CORE_CREATED_OBJECT_PROFILES[1].capability_class,
            CoreCapabilityClass.CREATED_HELPER_DELETE_OPERATION,
        )

    def test_1_existing_references_are_byte_identical(self):
        existing = [reference(p) for p in existing_profiles()]
        self.assertEqual(len(existing), 33)
        self.assertEqual(hashlib.sha256(canonical(existing)).hexdigest(), EXISTING_REFERENCES_SHA256)
        # Compared, never trusted: every retained owner-signed reference at
        # the registry tip equals its compiled profile, for every carried
        # Core release, and none names a new profile.
        compiled = {item["capability_id"]: item for item in existing}
        for entry in signed_tip_entries():
            with self.subTest(version=entry["version"]):
                for item in entry["capabilities"]:
                    self.assertEqual(item, compiled[item["capability_id"]])
                self.assertFalse({item["capability_id"] for item in entry["capabilities"]} & set(NEW))

    def test_4_older_compiled_releases_never_carry_the_new_profiles(self):
        for version, _commit, _digest in core_profiles.SUPPORTED_CORE_RELEASES:
            with self.subTest(version=version):
                self.assertFalse(set(NEW) & {
                    capability
                    for item in core_profiles.compiled_exact_authority(version)
                    for capability in item.capability_ids
                })

    def test_5_probe_fingerprints_are_unchanged(self):
        for entry in signed_tip_entries():
            with self.subTest(version=entry["version"]):
                probe = known_probe_profile(entry["probe_profile_id"], entry["probe_profile_sha256"])
                self.assertIsNotNone(probe)
                self.assertEqual(probe.contract_fingerprint, entry["probe_profile_sha256"])


class RouteTests(unittest.TestCase):
    def prepared(self, identity: str):
        return SimpleNamespace(
            capability_id=identity, capability_identity=identity, operation=identity,
            target={"target_type": "synthetic", "target_id": "synthetic"},
        )

    def test_new_identities_map_only_to_their_profile(self):
        expected = {
            "create_scene_configuration_configuration": SCENE_CREATE_DELETE_PROFILE,
            "capture_created_scene_identity": SCENE_CREATE_DELETE_PROFILE,
            "delete_created_scene_configuration": SCENE_CREATE_DELETE_PROFILE,
            "capture_created_input_boolean_identity": CREATED_HELPER_DELETE_PROFILE,
            "capture_created_input_number_identity": CREATED_HELPER_DELETE_PROFILE,
            "delete_created_input_boolean": CREATED_HELPER_DELETE_PROFILE,
            "delete_created_input_number": CREATED_HELPER_DELETE_PROFILE,
            # Automation/script capture reads are already in delete v1.
            "capture_created_automation_identity": CONFIGURATION_DELETE_PROFILE,
            "capture_created_script_identity": CONFIGURATION_DELETE_PROFILE,
        }
        for identity, profile in expected.items():
            with self.subTest(identity=identity):
                requirements = set(f3_requirements(self.prepared(identity)))
                self.assertIn(profile, requirements)
                self.assertNotIn("core.unsupported_f3_operation", requirements)
                self.assertFalse((set(NEW) - {profile}) & requirements)
        # The scene update identity is unchanged: never the new profile.
        update = set(f3_requirements(self.prepared("update_scene_configuration_configuration")))
        self.assertIn(SCENE_CONFIGURATION_PROFILE, update)
        self.assertFalse(set(NEW) & update)
        # A near-miss identity is unsupported, not routed to a new profile.
        near = set(f3_requirements(self.prepared("delete_created_input_select")))
        self.assertIn("core.unsupported_f3_operation", near)
        self.assertFalse(set(NEW) & near)

    def test_scene_tool_requires_the_new_profile_only_for_create(self):
        self.assertEqual(
            static_tool_requirements(SCENE_CONFIGURATION_TOOL, {"action": "create", "scene_id": "x"}),
            (SCENE_CREATE_DELETE_PROFILE,),
        )
        for arguments in ({"scene_id": "x"}, {"action": "update", "scene_id": "x"}, {}):
            with self.subTest(arguments=arguments):
                self.assertNotIn(
                    SCENE_CREATE_DELETE_PROFILE,
                    static_tool_requirements(SCENE_CONFIGURATION_TOOL, arguments),
                )
        self.assertLessEqual(set(NEW), F3_ENTRY_ADMISSION_PROFILES)


class SignedSelectionTests(unittest.IsolatedAsyncioTestCase):
    """Test-only signer; one synthetic Core whose version each test selects."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.signer = CoreSigner()
        self.observed = 0

    async def asyncTearDown(self):
        from ha_mcp_engineering.capabilities import replace_dynamic_upstream_capabilities

        replace_dynamic_upstream_capabilities((), {})

    def entry(self, version: str, *, extended: bool, unknown: bool = False) -> dict:
        """The shipped-shape reference set: every existing runtime profile."""

        value = core_entry(version, typed_operations=True)
        value.update(probe_profile_id=OCTOBER.profile_id, probe_profile_sha256=OCTOBER.contract_fingerprint)
        # A journal refuses duplicate image digests; give each release its own.
        value["image_index_digest"] = "sha256:" + hashlib.sha256(version.encode()).hexdigest()
        refs = [reference(p) for p in existing_profiles()]
        if extended:
            refs += [reference(p) for p in CORE_CREATED_OBJECT_PROFILES]
        if unknown:
            refs.append({
                "capability_id": "core.synthetic_future_operation",
                "profile_id": "core_synthetic_future_operation_v1",
                "profile_version": 1,
                "adapter_id": "compiled-core-synthetic-future-operation-v1",
                "contract_fingerprint": "sha256:" + "9" * 64,
            })
        value["capabilities"] = refs
        return value

    async def observe(self, version: str, *entries: dict):
        from tests.test_core_2026_10_device_registry import probe_inputs

        # One fresh registry (and cache) per observation: each scenario is a
        # separate first-boot authority, never a sequence replacement.
        self.observed += 1
        raw = self.signer.journal_raw(entries=list(entries), revocations=[], generated_at=NOW)

        async def fetch(*_args):
            return raw

        registry = CoreReleaseRegistry(
            enabled=True, public_key=self.signer.public_key_base64,
            cache_path=Path(self.tmp.name) / f"registry-{self.observed}.json",
            fetcher=fetch, now=lambda: NOW,
        )
        self.assertTrue(await registry.refresh())
        core = CoreRuntime()

        class Source:
            async def capture_core_snapshot(self):
                evidence = capability_evidence_for_probes(
                    **probe_inputs(registry.probe_profile_for(version), version=version))
                return {"identity": CORE_IDENTITY, "connected": True, "authenticated": True,
                        "session_id": "synthetic-created-object-core",
                        "rest_config": {"version": version},
                        "websocket_auth_ok": {"type": "auth_ok", "ha_version": version},
                        "websocket_get_config": {"version": version},
                        "capability_evidence": evidence}

        core.configure(object(), source=Source(), release_registry=registry)
        health = await reconcile_through_gate(core)
        return core, health

    def admitted(self, core) -> set[str]:
        return set(core.health_snapshot()["compatible_capabilities"])

    async def test_3_shipped_reference_set_admits_existing_and_withholds_only_the_new(self):
        core, health = await self.observe(TARGET_CORE, self.entry(TARGET_CORE, extended=False))
        existing = {p.capability_id for p in existing_profiles()}
        self.assertEqual(self.admitted(core), existing)
        self.assertEqual(health["compatible_count"], len(existing))
        self.assertEqual(health["withheld_count"], 2)
        withheld = {item["capability_id"]: item for item in health["unavailable_capabilities"]}
        self.assertEqual(set(withheld), set(NEW))
        for capability in NEW:
            with self.subTest(capability=capability):
                self.assertEqual(withheld[capability]["reason_code"], "positive_authority_missing")
                self.assertIs(core.route_status((capability,))["available"], False)
                self.assertIsNone(core.acquire((capability,)))
        # Existing capabilities, including scene update and automation/script
        # delete (which also serves their create rollback), stay admitted.
        for capability in (SCENE_CONFIGURATION_PROFILE, CONFIGURATION_DELETE_PROFILE):
            self.assertIs(core.route_status((capability,))["available"], True)
        self.assertEqual(health["fallback_count"], 0)

    async def test_3_scene_create_is_refused_before_any_home_assistant_request(self):
        import tests.test_hamcp069a_entity_metadata_readmission as readmission

        core, _health = await self.observe(TARGET_CORE, self.entry(TARGET_CORE, extended=False))
        gateway, app = readmission.gateway_for(core)
        create = {"action": "create", "config_id": "synthetic_evening", "name": "Evening",
                  "entities": {"light.synthetic": {"state": "on"}}}
        body = await readmission.call(gateway, SCENE_CONFIGURATION_TOOL, create)
        # Until the scene create planner and its per-action pending-admission
        # wiring land, ``action`` is refused at target validation: no plan, no
        # Core lease, no dispatch, no Home Assistant request.
        self.assertIn(b"InvalidStaticToolTarget", body)
        self.assertIn(b'\\"plan_created\\": false', body)
        self.assertIn(b'\\"provider_dispatch_occurred\\": false', body)
        self.assertEqual(app.calls, [])
        self.assertEqual(core.health_snapshot()["issued_lease_count"], 0)
        # The existing update request on the same tool is unaffected.
        update = {key: value for key, value in create.items() if key != "action"}
        body = await readmission.call(gateway, SCENE_CONFIGURATION_TOOL, update)
        self.assertNotIn(b"CoreCapabilityUnavailable", body)
        self.assertEqual(len(app.calls), 1)

    async def test_2_extended_entry_keeps_existing_admission_and_admits_the_new(self):
        shipped, shipped_health = await self.observe(TARGET_CORE, self.entry(TARGET_CORE, extended=False))
        core, health = await self.observe(
            TARGET_CORE, self.entry(TARGET_CORE, extended=True, unknown=True))
        self.assertEqual(self.admitted(core), self.admitted(shipped) | set(NEW))
        self.assertEqual(health["compatible_count"], shipped_health["compatible_count"] + 2)
        self.assertEqual(health["withheld_count"], 0)
        for capability in NEW:
            self.assertIs(core.route_status((capability,))["available"], True)

    async def test_4_older_cores_never_admit_from_the_extended_registry(self):
        for version in OLDER_CORES:
            with self.subTest(version=version):
                entries = (self.entry(TARGET_CORE, extended=True), self.entry(version, extended=False))
                core, health = await self.observe(version, *entries)
                self.assertFalse(set(NEW) & self.admitted(core))
                self.assertEqual(self.admitted(core), {p.capability_id for p in existing_profiles()})
                for capability in NEW:
                    self.assertIsNone(core.acquire((capability,)))

    async def test_4_tampered_new_reference_stays_withheld(self):
        entry = self.entry(TARGET_CORE, extended=True)
        for item in entry["capabilities"]:
            if item["capability_id"] in NEW:
                item["contract_fingerprint"] = "sha256:" + "0" * 64
        core, _health = await self.observe(TARGET_CORE, entry)
        self.assertFalse(set(NEW) & self.admitted(core))


if __name__ == "__main__":
    unittest.main()
