import copy
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BETA_DIR = ROOT / "hass_mcp_engineering_beta"
sys.path.insert(0, str(BETA_DIR))

from ha_mcp_engineering.dependency.extraction import (  # noqa: E402
    extract_document_obligation_evidence,
)
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    DependencyObligation,
    DependencyScanResult,
    DynamicReference,
    OBLIGATION_LEDGER_MODEL,
    SourceCoverageItem,
)
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    DependencySourceProvider,
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.providers import (  # noqa: E402
    ProviderCapability,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    build_helper_dependency_risk_binding,
)


TARGET = "input_boolean.obligation_canary"


def _extract(config, *, source_id="obligation"):
    return extract_document_obligation_evidence(
        source_type="automation",
        source_id=source_id,
        source_entity_id=f"automation.{source_id}",
        source_name="Synthetic obligation fixture",
        source_state="on",
        config=config,
    )


def _coverage():
    return [
        SourceCoverageItem(
            "automation",
            "direct_ha_api",
            "automation_config",
            "complete",
            obligation_ledger_completeness="complete",
        ),
        SourceCoverageItem(
            "blueprint",
            "direct_ha_api",
            "blueprint_source",
            "complete",
            obligation_ledger_completeness="complete",
        ),
    ]


class _FakeProvider(DependencySourceProvider):
    provider_id = "direct_ha_api"
    capabilities = frozenset({ProviderCapability.DEPENDENCY_ANALYSIS})

    def __init__(self, result):
        self.result = result

    @property
    def available(self):
        return True

    async def scan(self):
        return copy.deepcopy(self.result)

    async def fetch(self, request):
        raise NotImplementedError


class WholeConfigurationObligationTests(unittest.TestCase):
    def test_static_and_cross_segment_template_dependencies_are_accounted(self):
        findings, dynamic, obligations = _extract(
            {
                "trigger": [
                    {
                        "platform": "state",
                        "entity_id": TARGET,
                    }
                ],
                "condition": [
                    {
                        "condition": "template",
                        "value_template": (
                            "{% set lookup = is_state %}"
                            f"{{{{ lookup('{TARGET}', 'on') }}}}"
                        ),
                    }
                ],
            }
        )

        self.assertEqual(dynamic, [])
        self.assertEqual(
            {item.target_entity_id for item in findings}, {TARGET}
        )
        exact = [
            item
            for item in obligations
            if item.outcome == "exact_dependency"
            and TARGET in item.exact_entity_ids
        ]
        self.assertGreaterEqual(len(exact), 2)
        self.assertTrue(
            any(
                item.obligation_kind == "structured_entity_reference"
                for item in exact
            )
        )
        self.assertTrue(
            any(item.obligation_kind == "global_is_state" for item in exact)
        )

    def test_trigger_context_uses_surrounding_configuration_provenance(self):
        _findings, dynamic, obligations = _extract(
            {
                "trigger": [
                    {"platform": "state", "entity_id": TARGET}
                ],
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {
                            "message": "{{ trigger.to_state.state }}"
                        },
                    }
                ],
            },
            source_id="trigger_context",
        )

        self.assertEqual(dynamic, [])
        context_obligations = [
            item
            for item in obligations
            if item.obligation_kind == "trigger_context"
        ]
        self.assertEqual(len(context_obligations), 1)
        self.assertEqual(
            context_obligations[0].exact_entity_ids, (TARGET,)
        )
        self.assertIn(
            "trigger.to_state",
            context_obligations[0].context_provenance,
        )

    def test_mixed_configuration_variable_retains_exact_and_opaque_provenance(self):
        _findings, dynamic, obligations = _extract(
            {
                "variables": {
                    "candidates": [
                        "sensor.exact_candidate",
                        "{{ dynamic_entity }}",
                    ]
                },
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ states(candidates) }}",
                    }
                ],
            },
            source_id="mixed_variable",
        )

        self.assertTrue(dynamic)
        self.assertTrue(
            any(
                "sensor.exact_candidate" in item.exact_entity_ids
                for item in obligations
            ),
            obligations,
        )
        self.assertTrue(
            any(
                item.outcome == "bounded_semantic_opaque"
                and item.lock_projection == "conservative"
                for item in obligations
            ),
            obligations,
        )

    def test_unresolved_blueprint_input_is_an_explicit_obligation(self):
        _findings, dynamic, obligations = _extract(
            {
                "use_blueprint": {
                    "path": "synthetic/fixture.yaml",
                    "input": {},
                },
                "condition": [
                    {
                        "condition": "state",
                        "entity_id": {
                            "__blueprint_input__": "guard_entity"
                        },
                        "state": "on",
                    }
                ],
            },
            source_id="blueprint_input",
        )

        self.assertTrue(dynamic)
        matching = [
            item
            for item in obligations
            if item.obligation_kind == "blueprint_input"
        ]
        self.assertEqual(1, len(matching), obligations)
        self.assertEqual("bounded_semantic_opaque", matching[0].outcome)
        self.assertEqual("conservative", matching[0].lock_projection)

    def test_parse_error_is_explicit_opacity_not_absence(self):
        findings, dynamic, obligations = _extract(
            {
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ states(variable) ",
                    }
                ]
            },
            source_id="parse_error",
        )

        self.assertEqual(findings, [])
        self.assertTrue(dynamic)
        self.assertTrue(
            any(
                item.outcome == "bounded_semantic_opaque"
                and item.reason_code == "template_parse_error"
                for item in obligations
            )
        )

    def test_ordinary_message_template_is_explicitly_neutral(self):
        findings, dynamic, obligations = _extract(
            {
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {
                            "message": "Hello {{ person_name | title }}"
                        },
                    }
                ]
            },
            source_id="ordinary_message",
        )

        self.assertEqual(findings, [])
        # Unknown filters must remain opaque; ordinary literal interpolation is
        # separately proven neutral by the analyzer rather than disappearing.
        self.assertTrue(obligations)
        self.assertEqual(
            len(dynamic),
            sum(
                item.outcome
                in {"bounded_semantic_opaque", "coverage_failure"}
                for item in obligations
            ),
        )

    def test_display_alias_does_not_change_dependency_configuration_hash(self):
        first = _extract(
            {
                "alias": "First display name",
                "condition": [
                    {
                        "condition": "template",
                        "value_template": f"{{{{ is_state('{TARGET}', 'on') }}}}",
                    }
                ],
            },
            source_id="alias_drift",
        )[2]
        second = _extract(
            {
                "alias": "Second display name",
                "condition": [
                    {
                        "condition": "template",
                        "value_template": f"{{{{ is_state('{TARGET}', 'on') }}}}",
                    }
                ],
            },
            source_id="alias_drift",
        )[2]

        first_hashes = {
            item.configuration_fingerprint
            for item in first
            if TARGET in item.exact_entity_ids
        }
        second_hashes = {
            item.configuration_fingerprint
            for item in second
            if TARGET in item.exact_entity_ids
        }
        self.assertEqual(first_hashes, second_hashes)

    def test_context_candidate_overflow_is_explicit_coverage_failure(self):
        _findings, dynamic, obligations = _extract(
            {
                "trigger": [
                    {
                        "platform": "state",
                        "entity_id": f"sensor.context_{index}",
                    }
                    for index in range(129)
                ],
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {
                            "message": "{{ trigger.to_state.state }}"
                        },
                    }
                ],
            },
            source_id="context_overflow",
        )

        self.assertTrue(dynamic)
        self.assertTrue(
            any(
                item.outcome == "coverage_failure"
                and item.reason_code
                == "configuration_context_evidence_limit_exceeded"
                for item in obligations
            )
        )

    def test_state_changed_event_triggers_are_exact_or_explicitly_opaque(self):
        _findings, _dynamic, exact = _extract(
            {
                "trigger": [
                    {
                        "platform": "event",
                        "event_type": "state_changed",
                        "event_data": {"entity_id": TARGET},
                    }
                ]
            },
            source_id="state_changed_exact",
        )
        self.assertTrue(
            any(
                item.obligation_kind == "state_changed_event_trigger"
                and item.outcome == "exact_dependency"
                and TARGET in item.exact_entity_ids
                for item in exact
            ),
            exact,
        )

        cases = (
            {
                "trigger": [
                    {
                        "platform": "event",
                        "event_type": "state_changed",
                    }
                ]
            },
            {
                "action": [
                    {
                        "wait_for_trigger": [
                            {
                                "trigger": "event",
                                "event_type": "state_changed",
                            }
                        ]
                    }
                ]
            },
            {
                "trigger": [
                    {
                        "platform": "event",
                        "event_type": "{{ event_name }}",
                    }
                ]
            },
        )
        for index, config in enumerate(cases):
            with self.subTest(index=index):
                _findings, _dynamic, obligations = _extract(
                    config, source_id=f"state_changed_opaque_{index}"
                )
                self.assertTrue(
                    any(
                        item.obligation_kind
                        == "state_changed_event_trigger"
                        and item.outcome == "bounded_semantic_opaque"
                        and item.lock_projection == "conservative"
                        for item in obligations
                    ),
                    obligations,
                )

    def test_call_service_event_triggers_are_exact_opaque_or_disjoint(self):
        cases = (
            (
                {
                    "trigger": [
                        {
                            "platform": "event",
                            "event_type": "call_service",
                            "event_data": {
                                "domain": "input_boolean",
                                "service": "turn_on",
                                "service_data": {"entity_id": TARGET},
                            },
                        }
                    ]
                },
                "exact_dependency",
                "exact",
            ),
            (
                {
                    "trigger": [
                        {
                            "platform": "event",
                            "event_type": "call_service",
                        }
                    ]
                },
                "bounded_semantic_opaque",
                "conservative",
            ),
            (
                {
                    "action": [
                        {
                            "wait_for_trigger": [
                                {
                                    "trigger": "event",
                                    "event_type": "call_service",
                                    "event_data": {
                                        "domain": "input_boolean",
                                        "service": "turn_off",
                                    },
                                }
                            ]
                        }
                    ]
                },
                "bounded_semantic_opaque",
                "conservative",
            ),
            (
                {
                    "trigger": [
                        {
                            "platform": "event",
                            "event_type": "call_service",
                            "event_data": {
                                "domain": "light",
                                "service": "turn_on",
                            },
                        }
                    ]
                },
                "proven_target_exclusion",
                "none",
            ),
        )
        for index, (config, outcome, lock) in enumerate(cases):
            with self.subTest(index=index):
                _findings, _dynamic, obligations = _extract(
                    config, source_id=f"call_service_{index}"
                )
                matching = [
                    item
                    for item in obligations
                    if item.obligation_kind
                    == "call_service_event_trigger"
                ]
                self.assertEqual(1, len(matching), obligations)
                self.assertEqual(outcome, matching[0].outcome)
                self.assertEqual(lock, matching[0].lock_projection)
                if outcome == "exact_dependency":
                    self.assertIn(TARGET, matching[0].exact_entity_ids)


class ProviderObligationTests(unittest.IsolatedAsyncioTestCase):
    class Rest:
        async def request(self, method, path):
            if path == "/states":
                return [
                    {
                        "entity_id": "automation.good",
                        "state": "on",
                        "attributes": {"id": "good"},
                    },
                    {
                        "entity_id": "automation.bad",
                        "state": "on",
                        "attributes": {"id": "bad"},
                    },
                ]
            if path.endswith("/bad"):
                raise RuntimeError("synthetic unreadable automation")
            return {
                "condition": [
                    {
                        "condition": "template",
                        "value_template": f"{{{{ is_state('{TARGET}', 'on') }}}}",
                    }
                ],
                "action": [],
            }

    class WebSocket:
        async def command(self, payload):
            if payload == {"type": "config/entity_registry/list"}:
                return []
            raise AssertionError(payload)

    async def test_scan_retains_exact_and_read_failure_obligations(self):
        result = await DirectHaDependencyProvider(
            self.Rest(), self.WebSocket(), concurrency=2
        ).scan()

        self.assertTrue(
            any(
                item.outcome == "exact_dependency"
                and TARGET in item.exact_entity_ids
                for item in result.obligations
            )
        )
        failures = [
            item
            for item in result.obligations
            if item.outcome == "coverage_failure"
        ]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].source_entity_id, "automation.bad")
        self.assertEqual(
            failures[0].reason_code, "automation_config_unreadable"
        )

    async def test_automation_inventory_overflow_is_bounded_coverage_failure(self):
        class RecordingRest(self.Rest):
            def __init__(self):
                self.configuration_requests = 0

            async def request(self, method, path):
                if path.startswith("/config/automation/config/"):
                    self.configuration_requests += 1
                return await super().request(method, path)

        rest = RecordingRest()
        with patch(
            "ha_mcp_engineering.dependency.provider.MAX_AUTOMATION_SOURCES",
            1,
        ):
            result = await DirectHaDependencyProvider(
                rest, self.WebSocket(), concurrency=2
            ).scan()

        self.assertEqual(1, rest.configuration_requests)
        self.assertTrue(
            any(
                item.outcome == "coverage_failure"
                and item.reason_code
                == "automation_inventory_limit_exceeded"
                and item.limit_exceeded
                for item in result.obligations
            ),
            result.obligations,
        )
        automation_coverage = next(
            item
            for item in result.coverage
            if item.source_type == "automation"
        )
        self.assertNotEqual(
            "complete", automation_coverage.obligation_ledger_completeness
        )


class IndexObligationTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_dynamic_projection_overflow_does_not_poison_complete_ledger(self):
        _findings, _dynamic, extracted = _extract(
            {
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ states('sensor.unrelated') }}",
                    }
                ]
            },
            source_id="compatibility_projection",
        )
        base = next(
            item
            for item in extracted
            if item.outcome == "exact_dependency"
        )
        obligations = [
            replace(
                base,
                evidence_id=f"ev_compatibility_{index:02d}",
                source_id=f"compatibility_{index:02d}",
                source_entity_id=f"automation.compatibility_{index:02d}",
                expression_fingerprint=f"{index:064x}",
            )
            for index in range(4)
        ]
        dynamic = [
            DynamicReference(
                evidence_id=f"ev_dynamic_{index:02d}",
                source_type="automation",
                source_id=f"compatibility_{index:02d}",
                source_entity_id=f"automation.compatibility_{index:02d}",
                config_path="$.condition[0].value_template",
                warning="Legacy compatibility projection.",
                possible_entity_domains=("sensor",),
                possible_entity_ids=(f"sensor.unrelated_{index}",),
                candidate_resolution_kind="finite_literal_candidates",
                candidate_resolution_complete=True,
            )
            for index in range(4)
        ]
        result = DependencyScanResult(
            findings=[],
            dynamic_references=dynamic,
            target_metadata={},
            coverage=_coverage(),
            obligations=obligations,
            obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
        )

        with patch(
            "ha_mcp_engineering.dependency.index.MAX_DYNAMIC_REFERENCES",
            3,
        ):
            index = DependencyIndex(_FakeProvider(result))
            snapshot, _rebuilt, _lookup_ms = await index.get(refresh=True)
            metadata = index.evidence_metadata(snapshot)

        automation_coverage = next(
            item
            for item in snapshot.coverage
            if item.source_type == "automation"
        )
        self.assertEqual("partial", automation_coverage.completeness)
        self.assertEqual(
            "complete",
            automation_coverage.obligation_ledger_completeness,
        )
        self.assertEqual(1, snapshot.dynamic_reference_overflow_count)
        binding = build_helper_dependency_risk_binding(
            snapshot,
            entity_id=TARGET,
            index_metadata=metadata,
        )
        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual([], binding["relevant_downstream_object_ids"])

    async def test_overflow_is_fingerprinted_and_retains_coverage_failure(self):
        obligations = []
        for index in range(5):
            _findings, _dynamic, extracted = _extract(
                {
                    "condition": [
                        {
                            "condition": "template",
                            "value_template": (
                                "{{ is_state('input_boolean.member_"
                                f"{index}', 'on') }}}}"
                            ),
                        }
                    ]
                },
                source_id=f"overflow_{index}",
            )
            obligations.extend(
                item
                for item in extracted
                if item.outcome == "exact_dependency"
            )
        result = DependencyScanResult(
            findings=[],
            dynamic_references=[],
            target_metadata={},
            coverage=_coverage(),
            obligations=obligations,
        )

        with patch(
            "ha_mcp_engineering.dependency.index.MAX_DEPENDENCY_OBLIGATIONS",
            3,
        ):
            snapshot, _rebuilt, _lookup = await DependencyIndex(
                _FakeProvider(result)
            ).get(refresh=True)
            reordered_result = replace(
                result, obligations=list(reversed(obligations))
            )
            reordered, _rebuilt, _lookup = await DependencyIndex(
                _FakeProvider(reordered_result)
            ).get(refresh=True)

        self.assertEqual(len(snapshot.obligations), 3)
        self.assertEqual(snapshot.obligation_overflow_count, 3)
        self.assertEqual(len(snapshot.obligation_overflow_fingerprint), 64)
        self.assertTrue(
            any(
                item.outcome == "coverage_failure"
                and item.reason_code
                == "dependency_obligation_index_overflow"
                for item in snapshot.obligations
            )
        )
        automation_coverage = next(
            item
            for item in snapshot.coverage
            if item.source_type == "automation"
        )
        self.assertEqual(automation_coverage.completeness, "partial")
        self.assertEqual(
            snapshot.obligation_overflow_fingerprint,
            reordered.obligation_overflow_fingerprint,
        )
        self.assertEqual(snapshot.fingerprint, reordered.fingerprint)

    async def test_obligations_contribute_to_snapshot_fingerprint(self):
        _f, _d, first_obligations = _extract(
            {
                "condition": [
                    {
                        "condition": "template",
                        "value_template": f"{{{{ is_state('{TARGET}', 'on') }}}}",
                    }
                ]
            },
            source_id="fingerprint",
        )
        first_result = DependencyScanResult(
            [], [], {}, _coverage(), obligations=first_obligations
        )
        first, _rebuilt, _lookup = await DependencyIndex(
            _FakeProvider(first_result)
        ).get(refresh=True)
        changed = [
            replace(
                item,
                reason_code="synthetic_material_change",
            )
            for item in first_obligations
        ]
        second_result = DependencyScanResult(
            [], [], {}, _coverage(), obligations=changed
        )
        second, _rebuilt, _lookup = await DependencyIndex(
            _FakeProvider(second_result)
        ).get(refresh=True)

        self.assertNotEqual(first.fingerprint, second.fingerprint)


if __name__ == "__main__":
    unittest.main()
