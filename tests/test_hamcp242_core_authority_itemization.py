"""HAMCP-242: public Core authority health itemizes every withheld capability.

``get_server_health.home_assistant_core_authority`` used to publish counts
only, so a client read ``unavailable_count`` 3 and ``withheld_count`` 3 as six
blocked capabilities.  The projection now lists each held, quarantined and
unavailable profile once, with its reason code and the Engineering static
tools that require it, and states the count semantics.  The counts are
unchanged and must agree with the list.

The live case replays the committed, owner-signed sequence-14 entry for Core
2026.10.3 (re-signed with an ephemeral test key; no release authority) through
the production runtime, health registry and response envelope.  Everything is
offline and synthetic.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from core_registry_fixtures import CoreSigner, NOW  # noqa: E402
from support.core_gate import reconcile_through_gate  # noqa: E402

from ha_mcp_engineering.health import HealthRegistry  # noqa: E402
from ha_mcp_engineering.ha_core_readmission import coordinator as coordinator_module  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.coordinator import (  # noqa: E402
    COUNT_SEMANTICS,
    WITHHELD_CAPABILITIES_LIMIT,
    CoreReadmissionCoordinator,
    withheld_capability_entries,
)
from ha_mcp_engineering.ha_core_readmission.models import (  # noqa: E402
    CORE_IDENTITY,
    MAX_CAPABILITIES,
    MAX_REPORT_BYTES,
    CoreAuthorityStatus,
    CoreDisposition,
    CoreReadmissionError,
)
from ha_mcp_engineering.ha_core_readmission.profiles import (  # noqa: E402
    CORE_CAPABILITY_PROFILES,
    CORE_NOVEMBER_BATCH_PROFILES,
    CORE_RUNTIME_CAPABILITY_PROFILES,
)
from ha_mcp_engineering.ha_core_readmission import routes  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.registry import CoreReleaseRegistry  # noqa: E402
from ha_mcp_engineering.ha_core_readmission.routes import (  # noqa: E402
    DELEGATED_CORE_REQUIREMENTS,
    STATIC_TOOL_CORE_REQUIREMENTS,
    static_tools_requiring,
)
from ha_mcp_engineering.ha_core_readmission.runtime import CoreRuntime  # noqa: E402
from ha_mcp_engineering.tool_framework import run_structured  # noqa: E402
from ha_mcp_engineering.tools.registry import ENGINEERING_STATIC_TOOL_NAMES  # noqa: E402

from tests.test_ha_core_capability_auto_readmission import (  # noqa: E402
    _evidence,
    _observation,
    _verified_authority,
)


LIVE_CORE = "2026.10.3"
INPUT_SELECT = "core.typed_input_select_option_operation"
SCRIPT_TRACE = "core.script_trace_read"
HELPER_OPTIONS = "core.config_entry_helper_options_operation"
MISSING = "positive_authority_missing"

# The public block before HAMCP-242, in order; it must survive unchanged.
PRE_242_KEYS = (
    "model_version",
    "surface",
    "current_generation",
    "disposition",
    "compatible_count",
    "held_count",
    "quarantined_count",
    "unavailable_count",
    "withheld_count",
    "issued_lease_count",
    "active_commit_count",
    "retained_retirement_count",
    "capacity_exhaustion_count",
    "capacity_exhaustion_reason",
    "fallback_count",
    "configured",
    "initialized",
    "observed_core_version",
    "identity_agreement",
    "counters",
    "recovery",
    "release_registry",
    "recent_event_count",
)
NEW_KEYS = (
    "withheld_capabilities",
    "withheld_capabilities_truncated",
    "count_semantics",
)
ENTRY_KEYS = ("capability_id", "disposition", "reason_code", "affected_tools")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.]*$")

# The three profiles the signed sequence-14 2026.10.3 entry does not name,
# with the tools routes.py maps to each (pinned by hand, not derived).
EXPECTED_LIVE_WITHHELD = [
    {
        "capability_id": HELPER_OPTIONS,
        "disposition": "unavailable",
        "reason_code": MISSING,
        "affected_tools": ["create_helper_options_plan"],
    },
    {
        "capability_id": SCRIPT_TRACE,
        "disposition": "unavailable",
        "reason_code": MISSING,
        "affected_tools": ["get_script_trace", "list_script_traces"],
    },
    {
        "capability_id": INPUT_SELECT,
        "disposition": "unavailable",
        "reason_code": MISSING,
        "affected_tools": ["create_input_select_option_plan"],
    },
]

# Every runtime profile the static table maps, pinned by hand.  A profile
# absent here has no Engineering static tool (delegated reads only, or an
# F3/apply-time profile).
EXPECTED_AFFECTED_TOOLS = {
    "core.automation_baseline_metadata_read": ["capture_automation_baseline"],
    "core.automation_configuration_read": [
        "capture_automation_baseline",
        "get_automation_config",
        "list_automations",
    ],
    "core.automation_trace_read": [
        "automation_reliability_analysis",
        "capture_automation_trace",
        "change_impact_analysis",
        "get_automation_trace",
        "list_automation_traces",
    ],
    "core.basic_rest_read": [
        "capture_automation_baseline",
        "create_home_assistant_restart_plan",
        "dashboard_integrity_analysis",
        "get_configuration_definitions",
        "get_history",
        "get_history_slices",
        "get_logbook",
        "get_recorder_settings",
        "get_unregistered_entities",
    ],
    "core.basic_websocket_read": [
        "capture_automation_baseline",
        "create_addon_restart_plan",
        "create_backup_plan",
        "get_backup_inventory",
        "get_configuration_definitions",
        "get_error_log",
        "get_integration_inspection",
        "get_recorder_settings",
        "get_unregistered_entities",
        "list_blueprints",
    ],
    "core.config_entry_helper_options_operation": ["create_helper_options_plan"],
    "core.configuration_delete_operation": ["create_configuration_delete_plan"],
    "core.configuration_validation": [
        "check_config",
        "create_home_assistant_restart_plan",
        "create_reload_plan",
    ],
    "core.dashboard_configuration_read": [
        "create_dashboard_update_plan",
        "dashboard_integrity_analysis",
        "get_dashboard_config",
        "list_dashboards",
    ],
    "core.dependency_helper_planning": [
        "automation_reliability_analysis",
        "change_impact_analysis",
        "configuration_integrity_analysis",
        "create_change_plan",
        "create_configuration_delete_plan",
        "create_configuration_plan",
        "create_entity_availability_exposure_plan",
        "create_entity_metadata_plan",
        "create_helper_options_plan",
        "create_helper_state_plan",
        "create_input_select_option_plan",
        "create_label_plan",
        "entity_dependency_analysis",
        "handoff_generation",
        "incident_correlation",
    ],
    "core.direct_device_registry_read": [
        "capture_automation_baseline",
        "list_devices",
    ],
    "core.direct_entity_state_read": ["get_entity", "search_entities"],
    "core.entity_assistant_exposure_operation": [
        "create_entity_availability_exposure_plan",
    ],
    "core.entity_registry_availability_operation": [
        "create_entity_availability_exposure_plan",
    ],
    "core.entity_registry_metadata_operation": [
        "create_entity_metadata_plan",
        "create_label_plan",
    ],
    "core.integration_inspection_metadata_read": ["get_integration_inspection"],
    "core.label_registry_operation": ["create_label_plan"],
    "core.non_device_registry_read": [
        "capture_automation_baseline",
        "dashboard_integrity_analysis",
        "get_configuration_definitions",
        "get_unregistered_entities",
        "list_areas",
        "list_entity_registry",
    ],
    "core.script_trace_read": ["get_script_trace", "list_script_traces"],
    "core.state_service_discovery": [
        "create_reload_plan",
        "list_services",
        "search_services",
    ],
    "core.template_semantics": ["render_template"],
    "core.typed_input_select_option_operation": ["create_input_select_option_plan"],
}


def signed_sequence_14_entry(version: str = LIVE_CORE) -> dict:
    """The committed owner-signed entry, unchanged apart from re-signing."""

    registry = json.loads(
        (ROOT / "upstream-trust/ha-core-release-registry.json").read_text()
    )
    envelope = registry["envelopes"][-1]
    assert envelope["sequence"] == 14, envelope["sequence"]
    (entry,) = [item for item in envelope["entries"] if item["version"] == version]
    return deepcopy(entry)


def sequence_15_entry() -> dict:
    """Sequence 14 plus the three dormant November references (synthetic)."""

    entry = signed_sequence_14_entry()
    named = {item["capability_id"] for item in entry["capabilities"]}
    entry["capabilities"] = sorted(
        entry["capabilities"]
        + [
            {
                key: profile.to_mapping()[key]
                for key in (
                    "capability_id",
                    "profile_id",
                    "profile_version",
                    "adapter_id",
                    "contract_fingerprint",
                )
            }
            for profile in CORE_NOVEMBER_BATCH_PROFILES
            if profile.capability_id not in named
        ],
        key=lambda item: item["capability_id"],
    )
    return entry


def runtime_evidence() -> list[dict]:
    """Stable passing evidence for every runtime profile (synthetic)."""

    return [
        {
            "capability_id": profile.capability_id,
            "passed_checks": list(profile.required_checks),
            "semantic_fingerprint": (
                profile.contract_fingerprint
                if profile.capability_class.semantic
                else None
            ),
        }
        for profile in CORE_RUNTIME_CAPABILITY_PROFILES
    ]


class _LiveSource:
    async def capture_core_snapshot(self) -> dict:
        return {
            "identity": CORE_IDENTITY,
            "connected": True,
            "authenticated": True,
            "session_id": "synthetic-hamcp242-core-session",
            "rest_config": {"version": LIVE_CORE},
            "websocket_auth_ok": {"type": "auth_ok", "ha_version": LIVE_CORE},
            "websocket_get_config": {"version": LIVE_CORE},
            "capability_evidence": runtime_evidence(),
        }


def assert_entries_are_identifiers(case: unittest.TestCase, entries: list[dict]) -> None:
    for entry in entries:
        case.assertEqual(tuple(entry), ENTRY_KEYS)
        for value in (entry["capability_id"], entry["reason_code"], *entry["affected_tools"]):
            case.assertRegex(value, IDENTIFIER)
        case.assertIn(entry["disposition"], {"held", "quarantined", "unavailable"})
        case.assertEqual(entry["affected_tools"], sorted(entry["affected_tools"]))
        case.assertTrue(set(entry["affected_tools"]) <= set(ENGINEERING_STATIC_TOOL_NAMES))


class LiveCaseTests(unittest.IsolatedAsyncioTestCase):
    """Core 2026.10.3 under the signed sequence-14 entry (beta.43 live)."""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.signer = CoreSigner()
        self.raw: bytes | None = None

        async def fetch(*_args):
            if self.raw is None:
                raise OSError("Synthetic authority unavailable")
            return self.raw

        self.registry = CoreReleaseRegistry(
            enabled=True,
            public_key=self.signer.public_key_base64,
            cache_path=Path(self.tmp.name) / "registry.json",
            fetcher=fetch,
            now=lambda: NOW,
        )
        self.core = CoreRuntime()
        self.core.configure(object(), source=_LiveSource(), release_registry=self.registry)

    async def admit(self, entry: dict) -> dict:
        self.raw = self.signer.journal_raw(entries=[entry], revocations=[], generated_at=NOW)
        self.assertTrue(await self.registry.refresh())
        await reconcile_through_gate(self.core, "startup")
        return self.core.health_projection()

    async def public_block(self) -> dict:
        """The block as a client reads it, through the final envelope."""

        registry = HealthRegistry(core_readmission=self.core)
        text = await run_structured(
            "get_server_health",
            "Returned safe beta operational health.",
            lambda: registry.snapshot({"checked": False, "status": "not_checked"}),
        )
        envelope = json.loads(text)
        self.assertTrue(envelope["success"])
        return envelope["data"]["home_assistant_core_authority"]

    async def test_sequence_14_lists_exactly_the_three_pending_profiles(self):
        projection = await self.admit(signed_sequence_14_entry())
        self.assertEqual(projection["observed_core_version"], LIVE_CORE)
        # Counts byte-for-byte as beta.43 reports them.
        self.assertEqual(
            {key: projection[key] for key in (
                "compatible_count", "held_count", "quarantined_count",
                "unavailable_count", "withheld_count",
            )},
            {
                "compatible_count": 26,
                "held_count": 0,
                "quarantined_count": 0,
                "unavailable_count": 3,
                "withheld_count": 3,
            },
        )
        self.assertEqual(projection["withheld_capabilities"], EXPECTED_LIVE_WITHHELD)
        self.assertIs(projection["withheld_capabilities_truncated"], False)
        self.assertEqual(projection["count_semantics"], COUNT_SEMANTICS)
        self.assertEqual(
            projection["compatible_count"] + projection["withheld_count"],
            len(CORE_RUNTIME_CAPABILITY_PROFILES),
        )

        public = await self.public_block()
        self.assertEqual(public, json.loads(json.dumps(projection)))
        self.assertEqual(tuple(public), PRE_242_KEYS[:15] + NEW_KEYS + PRE_242_KEYS[15:])
        assert_entries_are_identifiers(self, public["withheld_capabilities"])
        # The three withheld profiles are still refused.
        for capability in (INPUT_SELECT, SCRIPT_TRACE, HELPER_OPTIONS):
            self.assertIsNone(self.core.acquire((capability,)))

    async def test_after_sequence_15_the_list_is_empty_and_compatible_is_29(self):
        projection = await self.admit(sequence_15_entry())
        self.assertEqual(projection["compatible_count"], 29)
        self.assertEqual(projection["withheld_count"], 0)
        self.assertEqual(projection["unavailable_count"], 0)
        self.assertEqual(projection["withheld_capabilities"], [])
        self.assertIs(projection["withheld_capabilities_truncated"], False)
        self.assertEqual((await self.public_block())["withheld_capabilities"], [])

    async def test_projection_adds_no_core_call_and_persists_nothing(self):
        await self.admit(signed_sequence_14_entry())
        before = sorted(path.name for path in Path(self.tmp.name).iterdir())
        source = self.core._source  # noqa: SLF001 - observation boundary
        calls = []
        original = source.capture_core_snapshot

        async def counted():
            calls.append(1)
            return await original()

        source.capture_core_snapshot = counted
        first = self.core.health_projection()
        second = await self.public_block()
        self.assertEqual(calls, [])
        self.assertEqual(first["withheld_capabilities"], second["withheld_capabilities"])
        self.assertEqual(sorted(path.name for path in Path(self.tmp.name).iterdir()), before)


class DispositionTests(unittest.TestCase):
    """Each disposition class produces its own exact entry."""

    def project(self, *, evidence=None, authority=None) -> dict:
        coordinator = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES)
        observation = (
            _observation(evidence=evidence) if evidence is not None else _observation()
        )
        coordinator.reconcile(
            observation,
            authority if authority is not None else _verified_authority(),
        )
        return coordinator.health_projection()

    def authority_without(self, *capability_ids: str):
        return _verified_authority(
            capability_ids={
                profile.capability_id for profile in CORE_CAPABILITY_PROFILES
            } - set(capability_ids)
        )

    def assert_counts_agree(self, projection: dict) -> None:
        entries = projection["withheld_capabilities"]
        for disposition in ("held", "quarantined", "unavailable"):
            self.assertEqual(
                projection[f"{disposition}_count"],
                sum(item["disposition"] == disposition for item in entries),
            )
        self.assertEqual(projection["withheld_count"], len(entries))
        self.assertEqual(
            projection["withheld_count"],
            projection["held_count"]
            + projection["quarantined_count"]
            + projection["unavailable_count"],
        )
        self.assertEqual(
            projection["compatible_count"] + projection["withheld_count"],
            len(CORE_CAPABILITY_PROFILES),
        )
        assert_entries_are_identifiers(self, entries)

    def test_all_compatible_gives_an_empty_list(self):
        projection = self.project()
        self.assertEqual(projection["compatible_count"], len(CORE_CAPABILITY_PROFILES))
        self.assertEqual(projection["withheld_capabilities"], [])
        self.assertIs(projection["withheld_capabilities_truncated"], False)
        self.assertEqual(projection["count_semantics"], COUNT_SEMANTICS)
        self.assert_counts_agree(projection)

    def test_held_entry(self):
        target = "core.state_service_discovery"
        authority = (
            *self.authority_without(target),
            *_verified_authority(
                capability_ids={target}, status=CoreAuthorityStatus.DENY_ONLY
            ),
        )
        projection = self.project(authority=authority)
        self.assertEqual(projection["held_count"], 1)
        self.assertEqual(
            projection["withheld_capabilities"],
            [{
                "capability_id": target,
                "disposition": "held",
                "reason_code": "verified_compatible_release",
                "affected_tools": ["create_reload_plan", "list_services", "search_services"],
            }],
        )
        self.assert_counts_agree(projection)

    def test_quarantined_entry(self):
        target = "core.basic_rest_read"
        projection = self.project(evidence=_evidence(unstable={target}))
        self.assertEqual(projection["quarantined_count"], 1)
        self.assertEqual(
            projection["withheld_capabilities"],
            [{
                "capability_id": target,
                "disposition": "quarantined",
                # A missing required check is a contract change, not instability.
                "reason_code": "capability_contract_changed",
                "affected_tools": EXPECTED_AFFECTED_TOOLS[target],
            }],
        )
        self.assert_counts_agree(projection)

    def test_unavailable_entries(self):
        missing, absent = "core.template_semantics", "core.configuration_validation"
        projection = self.project(
            evidence=_evidence(omit={absent}),
            authority=self.authority_without(missing),
        )
        self.assertEqual(projection["unavailable_count"], 2)
        self.assertEqual(
            projection["withheld_capabilities"],
            [
                {
                    "capability_id": absent,
                    "disposition": "unavailable",
                    "reason_code": "capability_evidence_missing",
                    "affected_tools": EXPECTED_AFFECTED_TOOLS[absent],
                },
                {
                    "capability_id": missing,
                    "disposition": "unavailable",
                    "reason_code": MISSING,
                    "affected_tools": ["render_template"],
                },
            ],
        )
        self.assert_counts_agree(projection)

    def test_mixed_dispositions_are_one_sorted_list(self):
        held, quarantined, unavailable = (
            "core.state_service_discovery",
            "core.basic_rest_read",
            "core.automation_trace_read",
        )
        authority = (
            *self.authority_without(held, unavailable),
            *_verified_authority(
                capability_ids={held}, status=CoreAuthorityStatus.DENY_ONLY
            ),
        )
        projection = self.project(
            evidence=_evidence(unstable={quarantined}), authority=authority
        )
        self.assertEqual(
            [(item["capability_id"], item["disposition"]) for item in projection["withheld_capabilities"]],
            [
                (unavailable, "unavailable"),
                (quarantined, "quarantined"),
                (held, "held"),
            ],
        )
        self.assertEqual(
            (projection["held_count"], projection["quarantined_count"], projection["unavailable_count"]),
            (1, 1, 1),
        )
        self.assert_counts_agree(projection)

    def test_global_refusal_lists_every_profile_within_the_report_bound(self):
        coordinator = CoreReadmissionCoordinator(CORE_RUNTIME_CAPABILITY_PROFILES)
        coordinator.reconcile(
            _observation(LIVE_CORE, config_version="2026.10.2", evidence=runtime_evidence()),
            (),
        )
        projection = coordinator.health_projection()
        entries = projection["withheld_capabilities"]
        self.assertEqual(projection["compatible_count"], 0)
        self.assertEqual(len(entries), len(CORE_RUNTIME_CAPABILITY_PROFILES))
        self.assertEqual(
            [item["capability_id"] for item in entries],
            sorted(profile.capability_id for profile in CORE_RUNTIME_CAPABILITY_PROFILES),
        )
        self.assertEqual({item["reason_code"] for item in entries}, {"core_version_disagreement"})
        self.assertIs(projection["withheld_capabilities_truncated"], False)
        encoded = json.dumps(projection, sort_keys=True).encode("utf-8")
        self.assertLessEqual(len(encoded), MAX_REPORT_BYTES)
        # The worst case stays a small share of the 60 000 character response.
        self.assertLess(len(encoded), 12_000)
        self.assert_counts_agree_runtime(projection)

    def assert_counts_agree_runtime(self, projection: dict) -> None:
        self.assertEqual(projection["withheld_count"], len(projection["withheld_capabilities"]))
        self.assertEqual(projection["unavailable_count"], len(CORE_RUNTIME_CAPABILITY_PROFILES))


def _assessment(held=(), quarantined=(), unavailable=()) -> dict:
    held, quarantined, unavailable = tuple(held), tuple(quarantined), tuple(unavailable)
    def items(values):
        return [{"capability_id": value, "reason_code": "synthetic_reason"} for value in values]

    return {
        "held_capabilities": items(held),
        "quarantined_capabilities": items(quarantined),
        "unavailable_capabilities": items(unavailable),
        "held_count": len(held),
        "quarantined_count": len(quarantined),
        "unavailable_count": len(unavailable),
        "withheld_count": len(held) + len(quarantined) + len(unavailable),
    }


class BoundAndConsistencyTests(unittest.TestCase):
    def test_limit_is_the_capability_bound(self):
        self.assertEqual(WITHHELD_CAPABILITIES_LIMIT, 64)
        self.assertEqual(WITHHELD_CAPABILITIES_LIMIT, MAX_CAPABILITIES)

    def test_sorted_by_capability_id_across_dispositions(self):
        entries, truncated = withheld_capability_entries(
            _assessment(held=("core.c",), quarantined=("core.a",), unavailable=("core.b",))
        )
        self.assertEqual(
            [(item["capability_id"], item["disposition"]) for item in entries],
            [("core.a", "quarantined"), ("core.b", "unavailable"), ("core.c", "held")],
        )
        self.assertIs(truncated, False)

    def test_exactly_64_is_not_truncated(self):
        names = [f"core.synthetic_{index:02d}" for index in range(64)]
        entries, truncated = withheld_capability_entries(_assessment(unavailable=reversed(names)))
        self.assertEqual([item["capability_id"] for item in entries], names)
        self.assertIs(truncated, False)

    def test_65_is_bounded_to_the_first_64_and_flagged(self):
        names = [f"core.synthetic_{index:02d}" for index in range(65)]
        split = (names[:20], names[20:40], names[40:])
        entries, truncated = withheld_capability_entries(
            _assessment(held=split[0][::-1], quarantined=split[1], unavailable=split[2][::-1])
        )
        self.assertEqual(len(entries), 64)
        self.assertEqual([item["capability_id"] for item in entries], names[:64])
        self.assertIs(truncated, True)

    def test_explicit_limit_flags_truncation(self):
        entries, truncated = withheld_capability_entries(
            _assessment(unavailable=("core.b", "core.a")), limit=1
        )
        self.assertEqual([item["capability_id"] for item in entries], ["core.a"])
        self.assertIs(truncated, True)

    def test_count_and_list_disagreement_refuses(self):
        for key in ("held_count", "quarantined_count", "unavailable_count", "withheld_count"):
            with self.subTest(key=key):
                assessment = _assessment(held=("core.a",), quarantined=("core.b",), unavailable=("core.c",))
                assessment[key] += 1
                with self.assertRaises(CoreReadmissionError) as raised:
                    withheld_capability_entries(assessment)
                self.assertEqual(raised.exception.code, "core_authority_projection_inconsistent")

    def test_duplicate_capability_across_dispositions_refuses(self):
        with self.assertRaises(CoreReadmissionError):
            withheld_capability_entries(_assessment(held=("core.a",), unavailable=("core.a",)))

    def test_unclassified_decision_refuses_the_assessment(self):
        coordinator = CoreReadmissionCoordinator(CORE_CAPABILITY_PROFILES)
        coordinator.reconcile(_observation(), _verified_authority())
        generation = coordinator._published  # noqa: SLF001 - invariant probe
        first, *rest = generation.decisions
        coordinator._published = replace(  # noqa: SLF001
            generation,
            decisions=(replace(first, disposition=CoreDisposition.VERIFYING), *rest),
        )
        for read in (coordinator.update_assessment, coordinator.health_projection):
            with self.subTest(read=read.__name__):
                with self.assertRaises(CoreReadmissionError) as raised:
                    read()
                self.assertEqual(raised.exception.code, "core_authority_projection_inconsistent")


class AffectedToolsTests(unittest.TestCase):
    def test_every_runtime_profile_maps_exactly(self):
        for profile in CORE_RUNTIME_CAPABILITY_PROFILES:
            with self.subTest(capability=profile.capability_id):
                self.assertEqual(
                    list(static_tools_requiring(profile.capability_id)),
                    EXPECTED_AFFECTED_TOOLS.get(profile.capability_id, []),
                )

    def test_pinned_table_names_only_runtime_profiles(self):
        self.assertTrue(
            set(EXPECTED_AFFECTED_TOOLS)
            <= {profile.capability_id for profile in CORE_RUNTIME_CAPABILITY_PROFILES}
        )

    def test_mapping_agrees_with_the_requirement_table_both_ways(self):
        listed = {
            (tool, capability)
            for capability in {
                value for values in STATIC_TOOL_CORE_REQUIREMENTS.values() for value in values
            }
            for tool in static_tools_requiring(capability)
        }
        declared = {
            (tool, capability)
            for tool, values in STATIC_TOOL_CORE_REQUIREMENTS.items()
            for capability in values
        }
        self.assertEqual(listed, declared)

    def test_script_trace_pair_and_dormant_tools(self):
        self.assertEqual(static_tools_requiring(SCRIPT_TRACE), ("get_script_trace", "list_script_traces"))
        self.assertEqual(static_tools_requiring(INPUT_SELECT), ("create_input_select_option_plan",))
        self.assertEqual(static_tools_requiring(HELPER_OPTIONS), ("create_helper_options_plan",))

    def test_delegated_and_unknown_names_are_not_listed(self):
        self.assertEqual(static_tools_requiring("core.unsupported_f3_operation"), ())
        self.assertEqual(static_tools_requiring("core.typed_fan_operation"), ())
        delegated = set(DELEGATED_CORE_REQUIREMENTS)
        for profile in CORE_RUNTIME_CAPABILITY_PROFILES:
            self.assertFalse(set(static_tools_requiring(profile.capability_id)) & delegated)

    def test_mapping_follows_the_requirement_table(self):
        original = dict(routes.STATIC_TOOL_CORE_REQUIREMENTS)
        self.addCleanup(lambda: (routes.STATIC_TOOL_CORE_REQUIREMENTS.clear(),
                                 routes.STATIC_TOOL_CORE_REQUIREMENTS.update(original)))
        routes.STATIC_TOOL_CORE_REQUIREMENTS.pop("get_script_trace")
        self.assertEqual(static_tools_requiring(SCRIPT_TRACE), ("list_script_traces",))

    def test_coordinator_uses_the_routes_mapping(self):
        self.assertIs(coordinator_module.static_tools_requiring, static_tools_requiring)


if __name__ == "__main__":
    unittest.main()
