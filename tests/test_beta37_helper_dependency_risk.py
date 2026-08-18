"""Dependency-aware risk contracts for the Beta 37 helper action."""

from __future__ import annotations

from dataclasses import replace
import time
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))

from ha_mcp_engineering.dependency.models import (  # noqa: E402
    AutomationReadFailure,
    AutomationActionRiskProfile,
    DependencyFinding,
    DependencyIndexSnapshot,
    SourceCoverageItem,
)
from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    extract_document,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    HelperDependencyRiskService,
    build_helper_dependency_risk_binding,
    helper_dependency_risk_assessment,
)
from ha_mcp_engineering.governance.risk import (  # noqa: E402
    automation_action_consequence_profile,
)


ENTITY_ID = "input_boolean.beta37_exact_action"


def action_profile(source: str, config: dict) -> AutomationActionRiskProfile:
    value = automation_action_consequence_profile(config)
    return AutomationActionRiskProfile(
        source_id=source,
        source_entity_id=(
            source if source.startswith("automation.") else f"automation.{source}"
        ),
        risk_level=value["risk_level"],
        physical_consequence=value["physical_consequence"],
        complete=value["complete"],
        truncated=value["truncated"],
        action_domains=tuple(value["action_domains"]),
        services=tuple(value["services"]),
        reason_codes=tuple(value["reason_codes"]),
        effect_projection_model=value["effect_projection_model"],
        effect_targets=tuple(value["effect_targets"]),
        effect_data=tuple(value["effect_data"]),
        effect_structure_fingerprint=value[
            "effect_structure_fingerprint"
        ],
        effect_projection_fingerprint=value[
            "effect_projection_fingerprint"
        ],
        effect_projection_clipped=value["effect_projection_clipped"],
        evidence_fingerprint=value["evidence_fingerprint"],
    )


def snapshot(
    profiles=(),
    *,
    automation_completeness: str = "complete",
    dynamic=(),
    findings=None,
    automation_warnings=(),
    automation_failed_item_count: int = 0,
    automation_read_failures=(),
    blueprint_completeness: str = "complete",
) -> DependencyIndexSnapshot:
    if findings is None:
        findings = tuple(
            DependencyFinding(
                evidence_id=f"ev_{index:024x}",
                target_entity_id=ENTITY_ID,
                source_type="automation",
                source_id=profile.source_id,
                source_entity_id=profile.source_entity_id,
                source_name=None,
                relation="trigger",
                config_path="$.trigger[0].entity_id",
            )
            for index, profile in enumerate(profiles, start=1)
        )
    return DependencyIndexSnapshot(
        fingerprint="a" * 64,
        generation=7,
        built_at_monotonic=time.monotonic(),
        built_at="2026-08-13T12:00:00+00:00",
        findings=findings,
        dynamic_references=tuple(dynamic),
        target_metadata={},
        coverage=(
            SourceCoverageItem(
                "automation",
                "direct_ha_api",
                "automation_config",
                automation_completeness,
                failed_item_count=automation_failed_item_count,
                warnings=list(automation_warnings),
            ),
            SourceCoverageItem(
                "blueprint",
                "direct_ha_api",
                "blueprint_source",
                blueprint_completeness,
            ),
        ),
        automation_action_profiles=tuple(profiles),
        automation_read_failures=tuple(automation_read_failures),
    )


def binding(value: DependencyIndexSnapshot):
    return build_helper_dependency_risk_binding(
        value,
        entity_id=ENTITY_ID,
        index_metadata={
            "freshness": "current",
            "evidence_stale": False,
            "invalidated": False,
        },
    )


class HelperDependencyRiskTests(unittest.IsolatedAsyncioTestCase):
    def test_complete_no_dependency_is_low_and_standard_eligible(self):
        observed = binding(snapshot())
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 7}}
        )

        self.assertEqual(observed["physical_consequence"], "none")
        self.assertEqual(observed["completeness"], "complete")
        self.assertTrue(observed["execution_eligible"])
        self.assertEqual(risk.level.value, "low")
        self.assertTrue(risk.apply_allowed)

    def test_benign_automation_is_complete_without_elevation(self):
        benign = action_profile(
            "benign",
            {
                "action": [
                    {
                        "service": "notify.mobile_app_disposable",
                        "data": {
                            "message": "bounded",
                            "title": "Bounded title",
                        },
                    }
                ]
            },
        )

        observed = binding(snapshot((benign,)))

        self.assertEqual(observed["physical_consequence"], "none")
        self.assertEqual(
            observed["relevant_downstream_object_ids"],
            ["automation.benign"],
        )
        self.assertEqual(
            observed["consequential_downstream_object_ids"], []
        )
        self.assertTrue(observed["evidence_complete"])

    def test_proven_benign_notification_remains_low_risk(self):
        profile = action_profile(
            "benign_notify",
            {
                "action": [
                    {
                        "service": "notify.mobile_app_disposable",
                        "data": {"message": "bounded"},
                    }
                ]
            },
        )

        self.assertEqual(profile.physical_consequence, "none")
        self.assertTrue(profile.complete)
        self.assertIn("proven_benign_action_family", profile.reason_codes)

    def test_reviewed_nonphysical_notification_controls_remain_low_risk(self):
        cases = (
            {"message": "clear_badge"},
            {
                "message": "clear_notification",
                "data": {"tag": "bounded-review-tag"},
            },
            {"message": "kiosk_hide_screensaver"},
            {"message": "kiosk_show_screensaver"},
            {"message": "update_complications"},
            {"message": "update_widgets"},
        )
        for payload in cases:
            with self.subTest(message=payload["message"]):
                profile = automation_action_consequence_profile(
                    {
                        "action": [
                            {
                                "service": "notify.mobile_app_disposable",
                                "data": payload,
                            }
                        ]
                    }
                )
                self.assertEqual(
                    profile["physical_consequence"], "none"
                )
                self.assertEqual(profile["risk_level"], "low")
                self.assertTrue(profile["complete"])
                self.assertIn(
                    "reviewed_nonphysical_notification_control",
                    profile["reason_codes"],
                )

        first_tag = "first-bounded-review-tag"
        second_tag = "second-bounded-review-tag"
        first = action_profile(
            "first_clear",
            {
                "action": [
                    {
                        "service": "notify.mobile_app_disposable",
                        "data": {
                            "message": "clear_notification",
                            "data": {"tag": first_tag},
                        },
                    }
                ]
            },
        )
        second = action_profile(
            "second_clear",
            {
                "action": [
                    {
                        "service": "notify.mobile_app_disposable",
                        "data": {
                            "message": "clear_notification",
                            "data": {"tag": second_tag},
                        },
                    }
                ]
            },
        )
        self.assertNotEqual(
            first.effect_projection_fingerprint,
            second.effect_projection_fingerprint,
        )
        self.assertNotIn(first_tag, str(first))
        self.assertNotIn(second_tag, str(second))

    def test_stateful_and_unreviewed_notification_controls_are_non_conclusive(self):
        for command in (
            "request_location_update",
            "remove_channel",
            "TTS",
            "kiosk_default",
            "kiosk_hide_camera",
            "kiosk_reload",
            "kiosk_set_brightness",
            "kiosk_set_volume",
            "kiosk_show_camera",
        ):
            with self.subTest(command=command):
                profile = automation_action_consequence_profile(
                    {
                        "action": [
                            {
                                "service": "notify.mobile_app_disposable",
                                "data": {"message": command},
                            }
                        ]
                    }
                )
                self.assertEqual(
                    profile["physical_consequence"], "unknown"
                )
                self.assertFalse(profile["complete"])
                self.assertIn(
                    "notification_control_effect",
                    profile["reason_codes"],
                )

    def test_reviewed_notification_control_rejects_unreviewed_payload(self):
        profile = automation_action_consequence_profile(
            {
                "action": [
                    {
                        "service": "notify.mobile_app_disposable",
                        "data": {
                            "message": "clear_notification",
                            "data": {
                                "tag": "bounded-review-tag",
                                "custom_effect": "synthetic",
                            },
                        },
                    }
                ]
            }
        )

        self.assertEqual(profile["physical_consequence"], "unknown")
        self.assertFalse(profile["complete"])
        self.assertIn(
            "notification_extension_unreviewed",
            profile["reason_codes"],
        )

    def test_mobile_notification_commands_are_never_harmless(self):
        for command in (
            "command_flashlight",
            "command_dnd",
            "command_bluetooth",
            "command_media",
            "command_launch_application",
        ):
            with self.subTest(command=command):
                profile = automation_action_consequence_profile(
                    {
                        "action": [
                            {
                                "service": "notify.mobile_app_disposable",
                                "data": {
                                    "message": command,
                                    "command": "turn_on",
                                },
                            }
                        ]
                    }
                )
                self.assertNotEqual(
                    profile["physical_consequence"], "none"
                )
                self.assertFalse(profile["complete"])
                self.assertIn(
                    "notification_command_effect",
                    profile["reason_codes"],
                )

        flattened = automation_action_consequence_profile(
            {
                "action": [
                    {
                        "service": "notify.mobile_app_disposable",
                        "message": "command_flashlight",
                        "command": "turn_on",
                    }
                ]
            }
        )
        self.assertNotEqual(flattened["physical_consequence"], "none")
        self.assertFalse(flattened["complete"])

    def test_dynamic_and_extended_notifications_are_non_conclusive(self):
        cases = (
            {
                "data": {"message": "{{ command_message }}"},
            },
            {
                "message": "{{ command_message }}",
                "data": {"message": "ordinary"},
            },
            {
                "data_template": {"message": "{{ message }}"},
            },
            {
                "data": {
                    "message": "ordinary",
                    "data": {"custom_effect": "synthetic"},
                },
            },
        )
        for extension in cases:
            with self.subTest(extension=extension):
                profile = automation_action_consequence_profile(
                    {
                        "action": [
                            {
                                "service": "notify.mobile_app_disposable",
                                **extension,
                            }
                        ]
                    }
                )
                self.assertEqual(
                    profile["physical_consequence"], "unknown"
                )
                self.assertFalse(profile["complete"])

    def test_common_physical_domains_are_never_declared_harmless(self):
        cases = {
            "switch": "turn_on",
            "light": "turn_on",
            "fan": "turn_on",
            "climate": "set_temperature",
            "cover": "open_cover",
            "lock": "lock",
            "valve": "open_valve",
            "water_heater": "set_temperature",
        }
        for domain, service in cases.items():
            with self.subTest(domain=domain):
                profile = automation_action_consequence_profile(
                    {
                        "action": [
                            {
                                "service": f"{domain}.{service}",
                                "target": {
                                    "entity_id": f"{domain}.disposable"
                                },
                            }
                        ]
                    }
                )
                self.assertNotEqual(
                    profile["physical_consequence"], "none"
                )
                self.assertTrue(profile["complete"])

    def test_generic_homeassistant_actions_require_exact_known_targets(self):
        for service in ("turn_on", "turn_off", "toggle"):
            exact = automation_action_consequence_profile(
                {
                    "action": [
                        {
                            "service": f"homeassistant.{service}",
                            "target": {"entity_id": "switch.heater"},
                        }
                    ]
                }
            )
            self.assertEqual(exact["physical_consequence"], "direct")
            self.assertTrue(exact["complete"])
            for selector in (
                "device_id",
                "area_id",
                "floor_id",
                "label_id",
            ):
                with self.subTest(service=service, selector=selector):
                    broad = automation_action_consequence_profile(
                        {
                            "action": [
                                {
                                    "service": f"homeassistant.{service}",
                                    "target": {selector: "bounded-id"},
                                }
                            ]
                        }
                    )
                    self.assertEqual(
                        broad["physical_consequence"], "unknown"
                    )
                    self.assertFalse(broad["complete"])

    def test_transitive_and_unrecognized_actions_are_unknown(self):
        cases = (
            {
                "action": [
                    {
                        "service": "scene.turn_on",
                        "target": {"entity_id": "scene.away"},
                    }
                ]
            },
            {
                "action": [
                    {
                        "service": "script.turn_on",
                        "target": {"entity_id": "script.arrive"},
                    }
                ]
            },
            {"action": [{"scene": "scene.away"}]},
            {
                "action": [
                    {
                        "service": "custom_domain.activate",
                        "target": {"entity_id": "custom_domain.item"},
                    }
                ]
            },
        )
        for config in cases:
            with self.subTest(config=config):
                profile = automation_action_consequence_profile(config)
                self.assertEqual(
                    profile["physical_consequence"], "unknown"
                )
                self.assertFalse(profile["complete"])

    def test_cover_action_is_consequential(self):
        cover = action_profile(
            "cover_path",
            {
                "action": [
                    {
                        "service": "cover.open_cover",
                        "target": {"entity_id": "cover.patio"},
                    }
                ]
            },
        )

        observed = binding(snapshot((cover,)))
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 7}}
        )

        self.assertEqual(observed["physical_consequence"], "direct")
        self.assertEqual(risk.level.value, "high")
        self.assertTrue(risk.apply_allowed)

    def test_entity_id_fallback_maps_to_configuration_lock_identity(self):
        profile = action_profile(
            "automation.benign_fallback",
            {
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {"message": "bounded"},
                    }
                ]
            },
        )

        observed = binding(snapshot((profile,)))

        self.assertEqual(
            observed["downstream_automation_resource_ids"],
            ["benign_fallback"],
        )

    def test_multiple_automations_retain_worst_consequence(self):
        benign = action_profile(
            "benign",
            {
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {"message": "bounded"},
                    }
                ]
            },
        )
        climate = action_profile(
            "climate",
            {
                "action": [
                    {
                        "service": "climate.set_temperature",
                        "target": {"entity_id": "climate.disposable"},
                    }
                ]
            },
        )
        security = action_profile(
            "security",
            {
                "action": [
                    {
                        "service": "lock.lock",
                        "target": {"entity_id": "lock.disposable"},
                    }
                ]
            },
        )

        observed = binding(snapshot((benign, climate, security)))

        self.assertEqual(
            observed["physical_consequence"], "safety_critical"
        )
        self.assertEqual(
            observed["consequential_downstream_object_ids"],
            ["automation.climate", "automation.security"],
        )

    def test_unavailable_automation_source_never_claims_conclusive_low(self):
        observed = binding(
            snapshot(automation_completeness="unavailable")
        )
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 7}}
        )

        self.assertFalse(observed["evidence_complete"])
        self.assertNotEqual(observed["completeness"], "complete")
        self.assertEqual(risk.level.value, "high")
        self.assertFalse(risk.apply_allowed)

    def test_unrelated_partial_coverage_does_not_poison_target(self):
        observed = binding(
            snapshot(
                blueprint_completeness="partial",
                automation_warnings=(
                    "1 unrelated blueprint could not be read.",
                ),
            )
        )

        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["physical_consequence"], "none")
        self.assertEqual(
            observed["coverage_diagnostics"][1]["completeness"],
            "partial",
        )

    def test_unreadable_automation_identity_prevents_complete_evidence(self):
        failure = AutomationReadFailure(
            source_id="unreadable",
            source_entity_id="automation.unreadable",
            reason_code="automation_config_unreadable",
        )
        observed = binding(
            snapshot(
                automation_completeness="partial",
                automation_failed_item_count=1,
                automation_read_failures=(failure,),
            )
        )

        self.assertFalse(observed["evidence_complete"])
        self.assertFalse(observed["execution_eligible"])
        self.assertEqual(observed["physical_consequence"], "unknown")
        self.assertEqual(observed["unreadable_automation_count"], 1)
        self.assertEqual(
            observed["unreadable_automation_ids"],
            ["automation.unreadable"],
        )

    def test_provider_partial_failed_count_cannot_become_complete(self):
        observed = binding(
            snapshot(
                automation_completeness="partial",
                automation_failed_item_count=1,
            )
        )

        self.assertFalse(observed["evidence_complete"])
        self.assertEqual(observed["unreadable_automation_count"], 1)
        self.assertTrue(
            observed["unreadable_automation_ids"][0].startswith(
                "unidentified_sha256:"
            )
        )

    def test_unreadable_automation_evidence_is_bounded(self):
        failures = tuple(
            AutomationReadFailure(
                source_id=f"unreadable_{index:03d}",
                source_entity_id=f"automation.unreadable_{index:03d}",
                reason_code="automation_config_unreadable",
            )
            for index in range(60)
        )
        observed = binding(
            snapshot(
                automation_completeness="partial",
                automation_failed_item_count=60,
                automation_read_failures=failures,
            )
        )

        self.assertEqual(observed["completeness"], "truncated")
        self.assertFalse(observed["execution_eligible"])
        self.assertEqual(observed["unreadable_automation_count"], 60)
        self.assertLessEqual(len(observed["unreadable_automation_ids"]), 50)
        self.assertTrue(
            observed["unreadable_automation_ids"][-1].startswith(
                "overflow_sha256:"
            )
        )

    def test_relevant_missing_action_profile_remains_non_conclusive(self):
        finding = DependencyFinding(
            evidence_id="ev_" + "1" * 24,
            target_entity_id=ENTITY_ID,
            source_type="automation",
            source_id="relevant_unreadable",
            source_entity_id="automation.relevant_unreadable",
            source_name=None,
            relation="condition",
            config_path="$.condition[0].entity_id",
        )

        observed = binding(
            snapshot(
                automation_completeness="partial",
                findings=(finding,),
            )
        )

        self.assertFalse(observed["evidence_complete"])
        self.assertEqual(observed["physical_consequence"], "unknown")

    def test_unrelated_dynamic_reference_does_not_poison_target(self):
        findings, dynamic = extract_document(
            source_type="automation",
            source_id="unrelated",
            source_entity_id="automation.unrelated",
            config={
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ states('sensor.' ~ room) }}",
                    }
                ]
            },
        )
        self.assertEqual(findings, [])
        self.assertEqual(dynamic[0].possible_entity_domains, ("sensor",))

        observed = binding(snapshot(dynamic=tuple(dynamic)))

        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["unrelated_dynamic_reference_count"], 1)
        self.assertEqual(
            observed["target_relevant_dynamic_reference_count"], 0
        )

    def test_compound_dynamic_domain_expressions_remain_unconstrained(self):
        expressions = (
            "'sensor.' ~ room if use_sensor else helper_entity",
            "'sensor.' ~ room and helper_entity",
            "'sensor.' ~ room or helper_entity",
            "('sensor.' ~ room)",
            "'sensor.' ~ room | lower",
        )
        for index, expression in enumerate(expressions):
            with self.subTest(expression=expression):
                findings, dynamic = extract_document(
                    source_type="automation",
                    source_id=f"ambiguous_{index}",
                    source_entity_id=f"automation.ambiguous_{index}",
                    config={
                        "condition": [
                            {
                                "condition": "template",
                                "value_template": (
                                    "{{ states(" + expression + ") }}"
                                ),
                            }
                        ]
                    },
                )
                self.assertEqual(findings, [])
                self.assertEqual(len(dynamic), 1)
                self.assertIsNone(
                    dynamic[0].possible_entity_domains
                )

                observed = binding(snapshot(dynamic=tuple(dynamic)))

                self.assertFalse(observed["evidence_complete"])
                self.assertFalse(observed["execution_eligible"])
                self.assertEqual(
                    observed["physical_consequence"], "unknown"
                )

    def test_dynamic_reference_to_other_exact_helper_is_unrelated(self):
        findings, dynamic = extract_document(
            source_type="automation",
            source_id="unrelated_helper",
            source_entity_id="automation.unrelated_helper",
            config={
                "condition": [
                    {
                        "condition": "template",
                        "value_template": (
                            "{{ is_state('input_boolean.other_helper', 'on') }}"
                        ),
                    }
                ]
            },
        )
        self.assertEqual(dynamic, [])
        self.assertEqual(
            {item.target_entity_id for item in findings},
            {"input_boolean.other_helper"},
        )

        observed = binding(snapshot(findings=tuple(findings)))

        self.assertTrue(observed["evidence_complete"])
        self.assertEqual(observed["unrelated_dynamic_reference_count"], 0)

    def test_relevant_dynamic_reference_remains_non_conclusive(self):
        findings, dynamic = extract_document(
            source_type="automation",
            source_id="dynamic_guard",
            source_entity_id="automation.dynamic_guard",
            config={
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ states(entity_variable) }}",
                    }
                ]
            },
        )
        self.assertEqual(findings, [])
        self.assertIsNone(dynamic[0].possible_entity_domains)

        observed = binding(snapshot(dynamic=tuple(dynamic)))

        self.assertFalse(observed["evidence_complete"])
        self.assertEqual(observed["physical_consequence"], "unknown")
        self.assertEqual(
            observed["target_relevant_dynamic_reference_count"], 1
        )

    def test_new_target_exclusion_evidence_changes_material_fingerprint(self):
        baseline = binding(snapshot())
        _findings, dynamic = extract_document(
            source_type="automation",
            source_id="unrelated",
            config={
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ states('sensor.' ~ room) }}",
                    }
                ]
            },
        )

        observed = binding(snapshot(dynamic=tuple(dynamic)))

        self.assertNotEqual(
            observed["evidence_fingerprint"],
            baseline["evidence_fingerprint"],
        )
        self.assertEqual(
            observed["resolved_dynamic_reference_evidence"][0][
                "target_membership"
            ],
            "excluded",
        )

    def test_effect_relevant_changes_are_bound_but_alias_is_ignored(self):
        base_config = {
            "alias": "Display only",
            "action": [
                {
                    "service": "climate.set_temperature",
                    "target": {"entity_id": "climate.living_room"},
                    "data": {"temperature": 20},
                }
            ],
        }
        base = action_profile("climate", base_config)
        alias_only = action_profile(
            "climate", {**base_config, "alias": "Renamed display only"}
        )
        target_changed = action_profile(
            "climate",
            {
                **base_config,
                "action": [
                    {
                        "service": "climate.set_temperature",
                        "target": {"entity_id": "climate.nursery"},
                        "data": {"temperature": 20},
                    }
                ],
            },
        )
        data_changed = action_profile(
            "climate",
            {
                **base_config,
                "action": [
                    {
                        "service": "climate.set_temperature",
                        "target": {
                            "entity_id": "climate.living_room"
                        },
                        "data": {"temperature": 35},
                    }
                ],
            },
        )
        service_changed = action_profile(
            "climate",
            {
                **base_config,
                "action": [
                    {
                        "service": "climate.set_hvac_mode",
                        "target": {
                            "entity_id": "climate.living_room"
                        },
                        "data": {"hvac_mode": "heat"},
                    }
                ],
            },
        )
        selector_changed = action_profile(
            "climate",
            {
                **base_config,
                "action": [
                    {
                        "service": "climate.set_temperature",
                        "target": {"area_id": "nursery"},
                        "data": {"temperature": 20},
                    }
                ],
            },
        )

        self.assertEqual(
            base.evidence_fingerprint, alias_only.evidence_fingerprint
        )
        self.assertNotEqual(
            base.evidence_fingerprint, target_changed.evidence_fingerprint
        )
        self.assertNotEqual(
            base.evidence_fingerprint, data_changed.evidence_fingerprint
        )
        self.assertNotEqual(
            base.evidence_fingerprint, service_changed.evidence_fingerprint
        )
        self.assertNotEqual(
            base.evidence_fingerprint, selector_changed.evidence_fingerprint
        )
        self.assertIn("temperature=20", " ".join(base.effect_data))

    def test_sensitive_and_oversized_effect_data_contributes_without_leak(self):
        first_secret = "first-secret-value"
        second_secret = "second-secret-value"
        first = action_profile(
            "notify",
            {
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {
                            "message": "x" * 4_000,
                            "token": first_secret,
                            "pin": 1234,
                        },
                    }
                ]
            },
        )
        second = action_profile(
            "notify",
            {
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {
                            "message": "x" * 4_000,
                            "token": second_secret,
                        },
                    }
                ]
            },
        )

        self.assertNotEqual(
            first.effect_projection_fingerprint,
            second.effect_projection_fingerprint,
        )
        serialized = str(first)
        self.assertNotIn(first_secret, serialized)
        self.assertNotIn("pin=1234", serialized)
        self.assertNotIn("x" * 300, serialized)

    def test_stale_target_evidence_is_never_accepted(self):
        observed = build_helper_dependency_risk_binding(
            snapshot(),
            entity_id=ENTITY_ID,
            index_metadata={
                "freshness": "stale",
                "evidence_stale": True,
                "invalidated": False,
            },
        )

        self.assertEqual(observed["completeness"], "stale")
        self.assertFalse(observed["execution_eligible"])

    def test_large_target_inventory_is_bounded(self):
        profiles = tuple(
            action_profile(
                f"bounded_{index:03d}",
                {
                    "action": [
                        {
                            "service": "notify.notify",
                            "data": {"message": "bounded"},
                        }
                    ]
                },
            )
            for index in range(60)
        )

        observed = binding(snapshot(profiles))

        self.assertEqual(observed["completeness"], "truncated")
        self.assertFalse(observed["execution_eligible"])
        self.assertLessEqual(len(observed["downstream_profiles"]), 50)

    def test_truncated_profile_never_claims_conclusive_low(self):
        benign = action_profile(
            "benign",
            {
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {"message": "bounded"},
                    }
                ]
            },
        )
        truncated = replace(benign, complete=False, truncated=True)

        observed = binding(snapshot((truncated,)))

        self.assertEqual(observed["completeness"], "truncated")
        self.assertFalse(observed["execution_eligible"])
        self.assertNotEqual(observed["physical_consequence"], "none")

    def test_oversized_action_value_is_bounded_and_incomplete(self):
        oversized = "x" * 300 + ".turn_on"

        profile = automation_action_consequence_profile(
            {"action": [{"service": oversized}]}
        )

        self.assertTrue(profile["truncated"])
        self.assertFalse(profile["complete"])
        self.assertEqual(profile["physical_consequence"], "unknown")
        self.assertEqual(len(profile["services"]), 1)
        self.assertTrue(
            profile["services"][0].startswith("oversized_sha256:")
        )
        self.assertLessEqual(
            len(profile["services"][0].encode("utf-8")), 256
        )

    async def test_dependency_source_failure_is_bounded_and_conservative(self):
        class FailingIndex:
            async def get(self, *, refresh, min_source_epoch=None):
                self.refresh = refresh
                self.min_source_epoch = min_source_epoch
                raise RuntimeError("untrusted provider response")

        index = FailingIndex()
        observed = await HelperDependencyRiskService(index).assess(
            ENTITY_ID, refresh=True
        )
        risk = helper_dependency_risk_assessment(observed)

        self.assertTrue(index.refresh)
        # Planning reads are not fenced; only the post-lock preflight is.
        self.assertIsNone(index.min_source_epoch)
        self.assertEqual(observed["binding"]["completeness"], "failed")
        self.assertEqual(observed["binding"]["physical_consequence"], "unknown")
        self.assertEqual(risk.level.value, "high")
        self.assertFalse(risk.apply_allowed)
        self.assertNotIn(
            "untrusted provider response", str(observed)
        )

    def test_harmless_helper_is_not_blanket_high_risk(self):
        observed = binding(snapshot())
        risk = helper_dependency_risk_assessment(
            {"binding": observed, "provenance": {"generation": 7}}
        )

        self.assertEqual(risk.level.value, "low")
        self.assertEqual(risk.warnings, [])


if __name__ == "__main__":
    unittest.main()
