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
    discharge_resolved_blueprint_source_obligation,
    extract_document_obligation_evidence,
)
from ha_mcp_engineering.dependency.index import DependencyIndex  # noqa: E402
from ha_mcp_engineering.dependency.models import (  # noqa: E402
    DependencyObligation,
    DependencyScanResult,
    DynamicReference,
    OBLIGATION_LEDGER_MODEL,
    SourceCoverageItem,
    obligation_identity,
)
from ha_mcp_engineering.dependency.provider import (  # noqa: E402
    _BlueprintSourceEvidence,
    DependencySourceProvider,
    DirectHaDependencyProvider,
)
from ha_mcp_engineering.providers import (  # noqa: E402
    ProviderCapability,
)
from ha_mcp_engineering.governance.helper_dependency import (  # noqa: E402
    build_helper_dependency_risk_binding,
)
from tests.dependency_blueprint_fixtures import (  # noqa: E402
    LARGE_TEMPLATE_COUNT,
    large_sensor_light_structural_blueprint,
)


TARGET = "input_boolean.obligation_canary"
BLUEPRINT_CONTENT_SHA256 = "b" * 64



from ha_mcp_engineering.dependency.semantic_registry import (  # noqa: E402
    supported_home_assistant_versions,
)

# B39-136-R3b: fakes describe a supported instance so the version
# admission gate is not the subject of these tests.
SUPPORTED_HA_VERSION = supported_home_assistant_versions()[-1]

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

    def test_exact_configuration_variable_maps_and_lists_preserve_shape(self):
        _findings, _dynamic, ordinary = _extract(
            {
                "variables": {
                    "summary": {
                        "text": "ready",
                        "values": ["alpha", "beta"],
                    }
                },
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {
                            "message": (
                                "{{ summary.text }}:"
                                "{{ summary.values | count }}"
                            )
                        },
                    }
                ],
            },
            source_id="ordinary_context_values",
        )
        self.assertTrue(ordinary)
        self.assertTrue(
            all(
                item.outcome == "proven_dependency_neutral"
                for item in ordinary
            ),
            ordinary,
        )

        _findings, _dynamic, exact = _extract(
            {
                "variables": {
                    "summary": {
                        "entities": ["sensor.unrelated", TARGET]
                    }
                },
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ states(summary.entities[1]) }}",
                    }
                ],
            },
            source_id="exact_context_values",
        )
        self.assertTrue(
            any(TARGET in item.exact_entity_ids for item in exact),
            exact,
        )

        _findings, _dynamic, fixed_index = _extract(
            {
                "variables": {
                    "summary": [
                        "sensor.unrelated",
                        "{{ dynamic_entity }}",
                        TARGET,
                    ]
                },
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ states(summary[2]) }}",
                    }
                ],
            },
            source_id="fixed_context_index",
        )
        selector_obligations = [
            item
            for item in fixed_index
            if item.obligation_kind == "global_states"
        ]
        self.assertTrue(
            any(TARGET in item.exact_entity_ids for item in selector_obligations),
            fixed_index,
        )
        # The configured list proves the exact default candidate, while an
        # automation.trigger run-variable override remains a bounded runtime
        # alternative and therefore preserves conservative opacity.
        self.assertTrue(
            any(
                item.outcome == "bounded_semantic_opaque"
                for item in selector_obligations
            ),
            fixed_index,
        )

        _findings, _dynamic, exact_sibling = _extract(
            {
                "variables": {
                    "summary": {
                        "text": "ready",
                        "dynamic": "{{ unknown_text }}",
                    }
                },
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {"message": "{{ summary.text }}"},
                    }
                ],
            },
            source_id="exact_context_sibling",
        )
        self.assertTrue(exact_sibling)
        self.assertTrue(
            all(
                item.outcome == "proven_dependency_neutral"
                for item in exact_sibling
            ),
            exact_sibling,
        )

    def test_repeat_context_is_scoped_and_preserves_finite_item_provenance(self):
        _findings, _dynamic, obligations = _extract(
            {
                "action": [
                    {
                        "repeat": {
                            "for_each": [
                                {"entity_id": "sensor.unrelated"},
                                {"entity_id": TARGET},
                            ],
                            "sequence": [
                                {
                                    "condition": "template",
                                    "value_template": (
                                        "{{ repeat.index }}:"
                                        "{{ states(repeat.item.entity_id) }}"
                                    ),
                                }
                            ],
                        }
                    }
                ]
            },
            source_id="repeat_context_exact",
        )
        self.assertTrue(
            any(TARGET in item.exact_entity_ids for item in obligations),
            obligations,
        )

        _findings, _dynamic, opaque = _extract(
            {
                "action": [
                    {
                        "repeat": {
                            "for_each": "{{ dynamic_values }}",
                            "sequence": [
                                {
                                    "condition": "template",
                                    "value_template": "{{ states(repeat.item) }}",
                                }
                            ],
                        }
                    }
                ]
            },
            source_id="repeat_context_dynamic",
        )
        self.assertTrue(
            any(
                item.outcome == "bounded_semantic_opaque"
                and item.lock_projection == "conservative"
                for item in opaque
            ),
            opaque,
        )

        _findings, _dynamic, outside = _extract(
            {
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ repeat.index }}",
                    }
                ]
            },
            source_id="repeat_context_outside",
        )
        self.assertTrue(
            any(item.outcome == "bounded_semantic_opaque" for item in outside),
            outside,
        )

    def test_zone_trigger_context_uses_exact_configuration_provenance(self):
        _findings, _dynamic, trigger_obligations = _extract(
            {
                "trigger": [
                    {
                        "platform": "zone",
                        "entity_id": "person.synthetic",
                        "zone": "zone.home",
                        "event": "enter",
                    }
                ],
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ trigger.zone.state }}",
                    }
                ],
            },
            source_id="exact_zone_trigger_context",
        )
        self.assertTrue(
            any(
                "zone.home" in item.exact_entity_ids
                for item in trigger_obligations
            ),
            trigger_obligations,
        )

        _findings, _dynamic, wait_obligations = _extract(
            {
                "action": [
                    {
                        "wait_for_trigger": [
                            {
                                "platform": "zone",
                                "entity_id": "person.synthetic",
                                "zone": "zone.work",
                                "event": "leave",
                            }
                        ]
                    },
                    {
                        "condition": "template",
                        "value_template": "{{ wait.trigger.zone.state }}",
                    },
                ]
            },
            source_id="exact_zone_wait_context",
        )
        self.assertTrue(
            any(
                "zone.work" in item.exact_entity_ids
                for item in wait_obligations
            ),
            wait_obligations,
        )

    def test_action_variables_are_sequential_and_service_data_is_not_a_binding(self):
        _findings, _dynamic, obligations = _extract(
            {
                "action": [
                    {
                        "condition": "template",
                        "value_template": "{{ states(selected) }}",
                    },
                    {"variables": {"selected": TARGET}},
                    {
                        "condition": "template",
                        "value_template": "{{ states(selected) }}",
                    },
                    {"variables": {"selected": "sensor.rebound"}},
                    {
                        "condition": "template",
                        "value_template": "{{ states(selected) }}",
                    },
                ]
            },
            source_id="sequential_action_variables",
        )
        selector_obligations = [
            item
            for item in obligations
            if item.obligation_kind == "global_states"
        ]
        self.assertTrue(
            any(
                item.outcome == "bounded_semantic_opaque"
                and item.config_path == "$.action[0].value_template"
                for item in selector_obligations
            ),
            selector_obligations,
        )
        self.assertTrue(
            any(
                item.exact_entity_ids == (TARGET,)
                and item.config_path == "$.action[2].value_template"
                for item in selector_obligations
            ),
            selector_obligations,
        )
        self.assertTrue(
            any(
                item.exact_entity_ids == ("sensor.rebound",)
                and item.config_path == "$.action[4].value_template"
                for item in selector_obligations
            ),
            selector_obligations,
        )

        _findings, _dynamic, service_data = _extract(
            {
                "action": [
                    {
                        "service": "notify.notify",
                        "data": {"variables": {"selected": TARGET}},
                    },
                    {
                        "condition": "template",
                        "value_template": "{{ states(selected) }}",
                    },
                ]
            },
            source_id="service_data_not_variables_action",
        )
        self.assertTrue(
            any(
                item.outcome == "bounded_semantic_opaque"
                and item.obligation_kind == "global_states"
                and not item.exact_entity_ids
                for item in service_data
            ),
            service_data,
        )
        self.assertFalse(
            any(
                item.obligation_kind == "global_states"
                and TARGET in item.exact_entity_ids
                for item in service_data
            ),
            service_data,
        )

        enablement_cases = (
            (False, (TARGET,)),
            (True, ("sensor.rebound",)),
            ("{{ dynamic_enablement }}", (TARGET, "sensor.rebound")),
        )
        for enabled, expected in enablement_cases:
            with self.subTest(enabled=enabled):
                _findings, _dynamic, enablement = _extract(
                    {
                        "variables": {"selected": TARGET},
                        "action": [
                            {
                                "enabled": enabled,
                                "variables": {"selected": "sensor.rebound"},
                            },
                            {
                                "condition": "template",
                                "value_template": "{{ states(selected) }}",
                            },
                        ],
                    },
                    source_id="variables_action_enablement",
                )
                selectors = [
                    item
                    for item in enablement
                    if item.obligation_kind == "global_states"
                ]
                self.assertTrue(
                    any(item.exact_entity_ids == expected for item in selectors),
                    selectors,
                )

    def test_variables_action_values_follow_mapping_insertion_order(self):
        ordered_config = {
            "action": [
                {
                    "variables": {
                        "selected": TARGET,
                        "observed": "{{ states(selected) }}",
                    }
                }
            ]
        }
        _findings, _dynamic, ordered = _extract(
            ordered_config,
            source_id="ordered_variables_action",
        )
        self.assertTrue(
            any(
                item.obligation_kind == "global_states"
                and item.exact_entity_ids == (TARGET,)
                and item.config_path.endswith(".variables.observed")
                for item in ordered
            ),
            ordered,
        )

        reversed_config = {
            "action": [
                {
                    "variables": {
                        "observed": "{{ states(selected) }}",
                        "selected": TARGET,
                    }
                }
            ]
        }
        _findings, _dynamic, reversed_obligations = _extract(
            reversed_config,
            source_id="ordered_variables_action",
        )
        self.assertTrue(
            any(
                item.obligation_kind == "global_states"
                and item.outcome == "bounded_semantic_opaque"
                and item.config_path.endswith(".variables.observed")
                for item in reversed_obligations
            ),
            reversed_obligations,
        )
        self.assertNotEqual(
            ordered[0].configuration_fingerprint,
            reversed_obligations[0].configuration_fingerprint,
        )

    def test_variable_value_entity_keys_are_data_until_consumed(self):
        _findings, _dynamic, neutral = _extract(
            {
                "action": [
                    {
                        "variables": {
                            "summary": {
                                "entity_id": TARGET,
                                "message": "ready",
                            }
                        }
                    },
                    {
                        "service": "notify.notify",
                        "data": {"message": "{{ summary.message }}"},
                    },
                ]
            },
            source_id="variable_entity_key_neutral",
        )
        self.assertFalse(
            any(TARGET in item.exact_entity_ids for item in neutral),
            neutral,
        )

        _findings, _dynamic, consumed = _extract(
            {
                "action": [
                    {
                        "variables": {
                            "summary": {
                                "entity_id": TARGET,
                                "message": "ready",
                            }
                        }
                    },
                    {
                        "condition": "template",
                        "value_template": (
                            "{{ states(summary.entity_id) }}"
                        ),
                    },
                ]
            },
            source_id="variable_entity_key_consumed",
        )
        self.assertTrue(
            any(
                item.obligation_kind == "global_states"
                and item.exact_entity_ids == (TARGET,)
                for item in consumed
            ),
            consumed,
        )

    def test_parallel_variable_branches_do_not_transfer_laterally(self):
        _findings, _dynamic, obligations = _extract(
            {
                "variables": {"selected": TARGET},
                "action": [
                    {
                        "parallel": [
                            {
                                "variables": {
                                    "selected": "sensor.branch_local"
                                }
                            },
                            {
                                "condition": "template",
                                "value_template": "{{ states(selected) }}",
                            },
                        ]
                    }
                ],
            },
            source_id="parallel_variable_isolation",
        )
        self.assertTrue(
            any(
                item.obligation_kind == "global_states"
                and item.exact_entity_ids == (TARGET,)
                for item in obligations
            ),
            obligations,
        )

    def test_root_variable_defaults_retain_runtime_override_uncertainty(self):
        _findings, _dynamic, obligations = _extract(
            {
                "variables": {"selected": "sensor.default"},
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ states(selected) }}",
                    }
                ],
            },
            source_id="root_variable_runtime_override",
        )
        selectors = [
            item
            for item in obligations
            if item.obligation_kind == "global_states"
        ]
        self.assertTrue(
            any("sensor.default" in item.exact_entity_ids for item in selectors),
            selectors,
        )
        self.assertTrue(
            any(
                item.outcome == "bounded_semantic_opaque"
                and item.lock_projection == "conservative"
                for item in selectors
            ),
            selectors,
        )

    def test_dynamic_enabled_variables_keep_true_path_sequential(self):
        _findings, _dynamic, obligations = _extract(
            {
                "action": [
                    {"variables": {"selected": TARGET}},
                    {
                        "enabled": "{{ dynamic_enablement }}",
                        "variables": {
                            "selected": "sensor.executed",
                            "observed": "{{ states(selected) }}",
                        },
                    },
                    {
                        "condition": "template",
                        "value_template": "{{ states(selected) }}",
                    },
                ]
            },
            source_id="dynamic_enabled_variable_paths",
        )
        observed = [
            item
            for item in obligations
            if item.obligation_kind == "global_states"
            and item.config_path.endswith(".variables.observed")
        ]
        self.assertTrue(observed)
        self.assertTrue(
            all(TARGET not in item.exact_entity_ids for item in observed),
            observed,
        )
        self.assertTrue(
            any("sensor.executed" in item.exact_entity_ids for item in observed),
            observed,
        )
        subsequent = [
            item
            for item in obligations
            if item.obligation_kind == "global_states"
            and item.config_path.endswith("action[2].value_template")
        ]
        self.assertTrue(
            any(
                set(item.exact_entity_ids) == {TARGET, "sensor.executed"}
                for item in subsequent
            ),
            subsequent,
        )

    def test_disabled_action_wrapper_does_not_analyze_descendants(self):
        _findings, _dynamic, obligations = _extract(
            {
                "action": [
                    {
                        "enabled": False,
                        "sequence": [
                            {
                                "variables": {
                                    "observed": (
                                        "{{ states('" + TARGET + "') }}"
                                    )
                                }
                            }
                        ],
                    },
                    {
                        "service": "notify.notify",
                        "data": {"message": "ready"},
                    },
                ]
            },
            source_id="disabled_action_wrapper",
        )
        self.assertFalse(
            any(TARGET in item.exact_entity_ids for item in obligations),
            obligations,
        )

    def test_repeat_variants_and_nested_scope_preserve_context_boundaries(self):
        for repeat_config in (
            {
                "count": 2,
                "sequence": [
                    {
                        "service": "notify.notify",
                        "data": {"message": "{{ repeat.index }}"},
                    }
                ],
            },
            {
                "while": [
                    {
                        "condition": "template",
                        "value_template": "{{ repeat.index < 3 }}",
                    }
                ],
                "sequence": [
                    {
                        "service": "notify.notify",
                        "data": {"message": "{{ repeat.index }}"},
                    }
                ],
            },
            {
                "until": [
                    {
                        "condition": "template",
                        "value_template": "{{ repeat.index > 3 }}",
                    }
                ],
                "sequence": [
                    {
                        "service": "notify.notify",
                        "data": {"message": "{{ repeat.index }}"},
                    }
                ],
            },
        ):
            with self.subTest(repeat_config=repeat_config):
                _findings, _dynamic, obligations = _extract(
                    {"action": [{"repeat": repeat_config}]},
                    source_id="repeat_runtime_scalar",
                )
                self.assertTrue(obligations)
                self.assertFalse(
                    any(
                        item.obligation_kind == "global_states"
                        for item in obligations
                    ),
                    obligations,
                )

        _findings, _dynamic, nested = _extract(
            {
                "action": [
                    {
                        "repeat": {
                            "for_each": [TARGET],
                            "sequence": [
                                {
                                    "repeat": {
                                        "for_each": ["sensor.inner"],
                                        "sequence": [
                                            {
                                                "condition": "template",
                                                "value_template": (
                                                    "{{ states(repeat.item) }}"
                                                ),
                                            }
                                        ],
                                    }
                                },
                                {
                                    "condition": "template",
                                    "value_template": "{{ states(repeat.item) }}",
                                },
                            ],
                        }
                    }
                ]
            },
            source_id="nested_repeat_scope",
        )
        selectors = [
            item for item in nested if item.obligation_kind == "global_states"
        ]
        self.assertTrue(
            any(item.exact_entity_ids == ("sensor.inner",) for item in selectors),
            selectors,
        )
        self.assertTrue(
            any(item.exact_entity_ids == (TARGET,) for item in selectors),
            selectors,
        )

    def test_repeat_special_binding_respects_later_variables_actions(self):
        cases = (
            (True, (TARGET,), False),
            (False, ("sensor.original",), False),
            ("{{ dynamic_enablement }}", (), True),
        )
        for enabled, expected_exact, expected_opaque in cases:
            with self.subTest(enabled=enabled):
                _findings, _dynamic, obligations = _extract(
                    {
                        "action": [
                            {
                                "repeat": {
                                    "for_each": ["sensor.original"],
                                    "sequence": [
                                        {
                                            "enabled": enabled,
                                            "variables": {
                                                "repeat": {"item": TARGET}
                                            },
                                        },
                                        {
                                            "condition": "template",
                                            "value_template": (
                                                "{{ states(repeat.item) }}"
                                            ),
                                        },
                                    ],
                                }
                            }
                        ]
                    },
                    source_id="repeat_variable_precedence",
                )
                selectors = [
                    item
                    for item in obligations
                    if item.obligation_kind == "global_states"
                ]
                if expected_exact:
                    self.assertTrue(
                        any(
                            item.exact_entity_ids == expected_exact
                            for item in selectors
                        ),
                        selectors,
                    )
                self.assertEqual(
                    expected_opaque,
                    any(
                        item.outcome == "bounded_semantic_opaque"
                        for item in selectors
                    ),
                    selectors,
                )

        _findings, _dynamic, nested = _extract(
            {
                "action": [
                    {
                        "repeat": {
                            "for_each": ["sensor.outer"],
                            "sequence": [
                                {
                                    "variables": {
                                        "repeat": {"item": TARGET}
                                    }
                                },
                                {
                                    "repeat": {
                                        "for_each": ["sensor.inner"],
                                        "sequence": [
                                            {
                                                "condition": "template",
                                                "value_template": (
                                                    "{{ states(repeat.item) }}"
                                                ),
                                            }
                                        ],
                                    }
                                },
                                {
                                    "condition": "template",
                                    "value_template": "{{ states(repeat.item) }}",
                                },
                            ],
                        }
                    }
                ]
            },
            source_id="nested_repeat_variable_precedence",
        )
        selectors = [
            item for item in nested if item.obligation_kind == "global_states"
        ]
        self.assertTrue(
            any(item.exact_entity_ids == ("sensor.inner",) for item in selectors),
            selectors,
        )
        self.assertTrue(
            any(item.exact_entity_ids == (TARGET,) for item in selectors),
            selectors,
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

    def test_raw_blueprint_source_is_explicit_until_source_is_analyzed(self):
        config = {
            "use_blueprint": {
                "path": "synthetic/hardcoded_helper.yaml",
                "input": {},
            }
        }

        _findings, dynamic, obligations = _extract(
            config,
            source_id="raw_blueprint_source",
        )
        matching = [
            item
            for item in obligations
            if item.obligation_kind == "external_blueprint_source"
        ]
        self.assertEqual(1, len(matching), obligations)
        self.assertEqual("bounded_semantic_opaque", matching[0].outcome)
        self.assertEqual("conservative", matching[0].lock_projection)
        self.assertEqual(
            "blueprint_source_unavailable_to_local_analysis",
            matching[0].reason_code,
        )
        self.assertTrue(dynamic)

        resolved_config = {
            "condition": [
                {
                    "condition": "state",
                    "entity_id": TARGET,
                    "state": "on",
                }
            ]
        }
        (
            discharged,
            _resolved_findings,
            _resolved_dynamic,
            resolved,
            removed_dynamic_ids,
        ) = (
            discharge_resolved_blueprint_source_obligation(
                automation_config=config,
                resolved_blueprint_config=resolved_config,
                raw_obligations=obligations,
                source_id="raw_blueprint_source",
                blueprint_source_content_sha256=(
                    BLUEPRINT_CONTENT_SHA256
                ),
                source_entity_id="automation.raw_blueprint_source",
                source_name="Resolved blueprint fixture",
                source_state="on",
            )
        )
        self.assertTrue(resolved)
        boundary = next(
            item
            for item in discharged
            if item.obligation_kind == "external_blueprint_source"
        )
        self.assertEqual("proven_dependency_neutral", boundary.outcome)
        self.assertEqual(
            "blueprint_source_analyzed_by_obligation_ledger",
            boundary.reason_code,
        )
        self.assertEqual("none", boundary.lock_projection)
        self.assertIn(
            "blueprint_source_content_sha256:"
            + BLUEPRINT_CONTENT_SHA256,
            boundary.context_provenance,
        )
        self.assertEqual(1, len(removed_dynamic_ids))

        changed_source, *_rest = (
            discharge_resolved_blueprint_source_obligation(
                automation_config=config,
                resolved_blueprint_config=resolved_config,
                raw_obligations=obligations,
                source_id="raw_blueprint_source",
                blueprint_source_content_sha256="c" * 64,
            )
        )
        changed_boundary = next(
            item
            for item in changed_source
            if item.obligation_kind == "external_blueprint_source"
        )
        self.assertNotEqual(
            boundary.expression_fingerprint,
            changed_boundary.expression_fingerprint,
        )

        unchanged, _findings, _dynamic, _resolved, removed = (
            discharge_resolved_blueprint_source_obligation(
                automation_config={
                    "use_blueprint": {
                        "path": "synthetic/different.yaml",
                        "input": {},
                    }
                },
                resolved_blueprint_config=resolved_config,
                raw_obligations=obligations,
                source_id="raw_blueprint_source",
                blueprint_source_content_sha256=(
                    BLUEPRINT_CONTENT_SHA256
                ),
            )
        )
        self.assertFalse(removed)
        self.assertTrue(
            any(
                item.obligation_kind == "external_blueprint_source"
                and item.outcome == "bounded_semantic_opaque"
                for item in unchanged
            )
        )

        unchanged, _findings, _dynamic, _resolved, removed = (
            discharge_resolved_blueprint_source_obligation(
                automation_config=config,
                resolved_blueprint_config=resolved_config,
                raw_obligations=obligations,
                source_id="raw_blueprint_source",
                blueprint_source_content_sha256="invalid",
            )
        )
        self.assertFalse(removed)
        self.assertTrue(
            any(
                item.obligation_kind == "external_blueprint_source"
                and item.outcome == "coverage_failure"
                and item.reason_code == "blueprint_source_drift_detected"
                for item in unchanged
            )
        )

        with self.assertRaises(TypeError):
            extract_document_obligation_evidence(
                source_type="automation",
                source_id="bare_assertion",
                config=config,
                blueprint_source_resolved=True,
            )

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
            if path == "/config":
                return {"version": SUPPORTED_HA_VERSION}
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

    async def test_resolved_blueprint_source_replaces_local_opacity_with_exact_evidence(self):
        class BlueprintRest:
            async def request(self, method, path):
                if path == "/config":
                    return {"version": SUPPORTED_HA_VERSION}
                if path == "/states":
                    return [
                        {
                            "entity_id": "automation.blueprint_guard",
                            "state": "on",
                            "attributes": {"id": "blueprint_guard"},
                        }
                    ]
                return {
                    "use_blueprint": {
                        "path": "synthetic/hardcoded_helper.yaml",
                        "input": {},
                    }
                }

        parsed_blueprint = {
            "blueprint": {"name": "Hard-coded helper fixture"},
            "condition": [
                {
                    "condition": "state",
                    "entity_id": TARGET,
                    "state": "on",
                }
            ],
            "action": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": "cover.synthetic_garage"},
                }
            ],
        }
        with patch(
            "ha_mcp_engineering.dependency.provider."
            "_read_blueprint_source_with_status",
            return_value=_BlueprintSourceEvidence(
                config=parsed_blueprint,
                reason_code=None,
                source_path="synthetic/hardcoded_helper.yaml",
                content_sha256=BLUEPRINT_CONTENT_SHA256,
                content_bytes=512,
            ),
        ):
            result = await DirectHaDependencyProvider(
                BlueprintRest(), self.WebSocket(), concurrency=2
            ).scan()

        boundary = next(
            item
            for item in result.obligations
            if item.obligation_kind == "external_blueprint_source"
        )
        self.assertEqual("proven_dependency_neutral", boundary.outcome)
        self.assertEqual(
            "blueprint_source_analyzed_by_obligation_ledger",
            boundary.reason_code,
        )
        self.assertTrue(
            any(
                item.source_type == "blueprint"
                and item.outcome == "exact_dependency"
                and TARGET in item.exact_entity_ids
                for item in result.obligations
            ),
            result.obligations,
        )
        profile = next(
            item
            for item in result.automation_action_profiles
            if item.source_id == "blueprint_guard"
        )
        self.assertEqual("safety_critical", profile.physical_consequence)
        index = DependencyIndex(_FakeProvider(result))
        snapshot, _rebuilt, _lookup_ms = await index.get(refresh=True)
        binding = build_helper_dependency_risk_binding(
            snapshot,
            entity_id=TARGET,
            index_metadata={
                "freshness": "current",
                "evidence_stale": False,
                "invalidated": False,
            },
        )
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual("safety_critical", binding["physical_consequence"])
        self.assertIn(
            "blueprint_guard",
            binding["downstream_automation_resource_ids"],
        )

    async def test_shared_blueprint_source_is_read_once_per_scan(self):
        class SharedBlueprintRest:
            async def request(self, method, path):
                if path == "/config":
                    return {"version": SUPPORTED_HA_VERSION}
                if path == "/states":
                    return [
                        {
                            "entity_id": "automation.synthetic_shared_a",
                            "state": "on",
                            "attributes": {"id": "synthetic_shared_a"},
                        },
                        {
                            "entity_id": "automation.synthetic_shared_b",
                            "state": "on",
                            "attributes": {"id": "synthetic_shared_b"},
                        },
                    ]
                if path.startswith("/config/automation/config/"):
                    return {
                        "use_blueprint": {
                            "path": "synthetic/shared.yaml",
                            "input": {},
                        }
                    }
                raise AssertionError((method, path))

        evidence = _BlueprintSourceEvidence(
            config=large_sensor_light_structural_blueprint(),
            reason_code=None,
            source_path="synthetic/shared.yaml",
            content_sha256=BLUEPRINT_CONTENT_SHA256,
            content_bytes=256,
        )
        with patch(
            "ha_mcp_engineering.dependency.provider."
            "_read_blueprint_source_with_status",
            return_value=evidence,
        ) as reader:
            result = await DirectHaDependencyProvider(
                SharedBlueprintRest(), self.WebSocket(), concurrency=2
            ).scan()

        self.assertEqual(reader.call_count, 1)
        boundaries = [
            item
            for item in result.obligations
            if item.obligation_kind == "external_blueprint_source"
        ]
        self.assertEqual(
            {item.source_id for item in boundaries},
            {"synthetic_shared_a", "synthetic_shared_b"},
        )
        self.assertTrue(
            all(
                item.outcome == "proven_dependency_neutral"
                and (
                    "blueprint_source_content_sha256:"
                    + BLUEPRINT_CONTENT_SHA256
                )
                in item.context_provenance
                for item in boundaries
            )
        )
        blueprint_coverage = next(
            item
            for item in result.coverage
            if item.source_type == "blueprint"
        )
        self.assertEqual(blueprint_coverage.completeness, "complete")
        self.assertEqual(blueprint_coverage.failed_item_count, 0)
        by_consumer = {
            source_id: [
                item
                for item in result.obligations
                if item.source_type == "blueprint"
                and item.source_id == source_id
            ]
            for source_id in ("synthetic_shared_a", "synthetic_shared_b")
        }
        self.assertTrue(
            all(
                len(items) == LARGE_TEMPLATE_COUNT
                for items in by_consumer.values()
            )
        )
        self.assertTrue(
            all(
                item.blueprint_source_path == "synthetic/shared.yaml"
                and item.blueprint_source_sha256
                == BLUEPRINT_CONTENT_SHA256
                for items in by_consumer.values()
                for item in items
            )
        )
        first_identities = {
            obligation_identity(item)
            for item in by_consumer["synthetic_shared_a"]
        }
        second_identities = {
            obligation_identity(item)
            for item in by_consumer["synthetic_shared_b"]
        }
        self.assertEqual(len(first_identities), LARGE_TEMPLATE_COUNT)
        self.assertEqual(len(second_identities), LARGE_TEMPLATE_COUNT)
        self.assertTrue(first_identities.isdisjoint(second_identities))
        self.assertEqual(result.obligation_diagnostics, [])

    async def test_shared_source_consumers_remain_independently_attributable(self):
        class SharedInputRest:
            async def request(self, method, path):
                if path == "/config":
                    return {"version": SUPPORTED_HA_VERSION}
                if path == "/states":
                    return [
                        {
                            "entity_id": "automation.synthetic_safe",
                            "state": "on",
                            "attributes": {"id": "synthetic_safe"},
                        },
                        {
                            "entity_id": "automation.synthetic_opaque",
                            "state": "on",
                            "attributes": {"id": "synthetic_opaque"},
                        },
                    ]
                if path.endswith("/synthetic_safe"):
                    selected = "{{ 'synthetic non-entity text' }}"
                elif path.endswith("/synthetic_opaque"):
                    selected = "{{ states(entity_variable) }}"
                else:
                    raise AssertionError((method, path))
                return {
                    "use_blueprint": {
                        "path": "synthetic/shared-input.yaml",
                        "input": {"selected_template": selected},
                    }
                }

        evidence = _BlueprintSourceEvidence(
            config={
                "blueprint": {"name": "Synthetic shared input"},
                "actions": [
                    {
                        "value_template": {
                            "__blueprint_input__": "selected_template"
                        }
                    }
                ],
            },
            reason_code=None,
            source_path="synthetic/shared-input.yaml",
            content_sha256=BLUEPRINT_CONTENT_SHA256,
            content_bytes=256,
        )
        with patch(
            "ha_mcp_engineering.dependency.provider."
            "_read_blueprint_source_with_status",
            return_value=evidence,
        ) as reader:
            result = await DirectHaDependencyProvider(
                SharedInputRest(), self.WebSocket(), concurrency=2
            ).scan()

        self.assertEqual(reader.call_count, 1)
        safe = [
            item
            for item in result.obligations
            if item.source_type == "blueprint"
            and item.source_id == "synthetic_safe"
        ]
        opaque = [
            item
            for item in result.obligations
            if item.source_type == "blueprint"
            and item.source_id == "synthetic_opaque"
        ]
        self.assertTrue(safe)
        self.assertTrue(opaque)
        self.assertFalse(
            any(
                item.consumer_source_id == "synthetic_safe"
                for item in result.obligation_diagnostics
            )
        )
        opaque_diagnostics = [
            item
            for item in result.obligation_diagnostics
            if item.consumer_source_id == "synthetic_opaque"
            and item.source_type == "blueprint"
        ]
        self.assertEqual(len(opaque_diagnostics), 1)
        self.assertEqual(
            opaque_diagnostics[0].diagnostic_code,
            "unsupported_dynamic_entity_lookup",
        )
        self.assertEqual(
            opaque_diagnostics[0].blueprint_source_sha256,
            BLUEPRINT_CONTENT_SHA256,
        )
        blueprint_coverage = next(
            item
            for item in result.coverage
            if item.source_type == "blueprint"
        )
        self.assertEqual(blueprint_coverage.completeness, "partial")
        self.assertEqual(
            blueprint_coverage.failed_item_count,
            len(opaque_diagnostics),
        )
        self.assertEqual(
            blueprint_coverage.obligation_ledger_failed_item_count,
            len(opaque_diagnostics),
        )

    async def test_multiple_failures_have_exact_diagnostic_correspondence(self):
        class MultipleFailureRest:
            async def request(self, method, path):
                if path == "/config":
                    return {"version": SUPPORTED_HA_VERSION}
                if path == "/states":
                    return [
                        {
                            "entity_id": "automation.synthetic_failures",
                            "state": "on",
                            "attributes": {"id": "synthetic_failures"},
                        }
                    ]
                if path.endswith("/synthetic_failures"):
                    return {
                        "use_blueprint": {
                            "path": "synthetic/failures.yaml",
                            "input": {},
                        }
                    }
                raise AssertionError((method, path))

        evidence = _BlueprintSourceEvidence(
            config={
                "actions": [
                    {
                        "value_template": (
                            "{{ states(entity_variable) }}"
                        )
                    }
                    for _index in range(3)
                ]
            },
            reason_code=None,
            source_path="synthetic/failures.yaml",
            content_sha256=BLUEPRINT_CONTENT_SHA256,
            content_bytes=256,
        )
        with patch(
            "ha_mcp_engineering.dependency.provider."
            "_read_blueprint_source_with_status",
            return_value=evidence,
        ):
            result = await DirectHaDependencyProvider(
                MultipleFailureRest(), self.WebSocket(), concurrency=1
            ).scan()

        diagnostics = [
            item
            for item in result.obligation_diagnostics
            if item.source_type == "blueprint"
        ]
        self.assertEqual(len(diagnostics), 3)
        self.assertEqual(
            len({item.diagnostic_id for item in diagnostics}), 3
        )
        self.assertTrue(
            all(
                item.diagnostic_code
                == "unsupported_dynamic_entity_lookup"
                and not item.evidence_complete
                for item in diagnostics
            )
        )
        coverage = next(
            item
            for item in result.coverage
            if item.source_type == "blueprint"
        )
        self.assertEqual(coverage.failed_item_count, len(diagnostics))
        self.assertEqual(
            coverage.obligation_ledger_failed_item_count,
            len(diagnostics),
        )

    async def test_source_hash_change_invalidates_scan_local_identity(self):
        class OneBlueprintRest:
            async def request(self, method, path):
                if path == "/config":
                    return {"version": SUPPORTED_HA_VERSION}
                if path == "/states":
                    return [
                        {
                            "entity_id": "automation.synthetic_hash",
                            "state": "on",
                            "attributes": {"id": "synthetic_hash"},
                        }
                    ]
                if path.endswith("/synthetic_hash"):
                    return {
                        "use_blueprint": {
                            "path": "synthetic/hash.yaml",
                            "input": {},
                        }
                    }
                raise AssertionError((method, path))

        source = {"actions": [{"value_template": "{{ 'safe' }}"}]}
        evidence = [
            _BlueprintSourceEvidence(
                source,
                None,
                "synthetic/hash.yaml",
                "b" * 64,
                128,
            ),
            _BlueprintSourceEvidence(
                source,
                None,
                "synthetic/hash.yaml",
                "c" * 64,
                128,
            ),
        ]
        provider = DirectHaDependencyProvider(
            OneBlueprintRest(), self.WebSocket(), concurrency=1
        )
        with patch(
            "ha_mcp_engineering.dependency.provider."
            "_read_blueprint_source_with_status",
            side_effect=evidence,
        ) as reader:
            first = await provider.scan()
            second = await provider.scan()

        self.assertEqual(reader.call_count, 2)
        first_blueprint = [
            item
            for item in first.obligations
            if item.source_type == "blueprint"
        ]
        second_blueprint = [
            item
            for item in second.obligations
            if item.source_type == "blueprint"
        ]
        self.assertEqual(
            {item.blueprint_source_sha256 for item in first_blueprint},
            {"b" * 64},
        )
        self.assertEqual(
            {item.blueprint_source_sha256 for item in second_blueprint},
            {"c" * 64},
        )
        self.assertNotEqual(
            {obligation_identity(item) for item in first_blueprint},
            {obligation_identity(item) for item in second_blueprint},
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
            home_assistant_version=SUPPORTED_HA_VERSION,
            home_assistant_version_status="observed",
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
        self.assertEqual("complete", automation_coverage.completeness)
        self.assertEqual(
            "complete",
            automation_coverage.obligation_ledger_completeness,
        )
        self.assertEqual(1, snapshot.dynamic_reference_overflow_count)
        self.assertTrue(
            any(
                "Dynamic compatibility evidence exceeded its bounded projection"
                in warning
                for warning in automation_coverage.warnings
            )
        )
        binding = build_helper_dependency_risk_binding(
            snapshot,
            entity_id=TARGET,
            index_metadata=metadata,
        )
        self.assertTrue(binding["coverage_complete"])
        self.assertTrue(binding["execution_eligible"])
        self.assertEqual([], binding["relevant_downstream_object_ids"])

    async def test_public_projection_overflow_preserves_authoritative_failures(self):
        _findings, _dynamic, extracted = _extract(
            {
                "condition": [
                    {
                        "condition": "template",
                        "value_template": "{{ states(entity_variable) }}",
                    }
                ]
            },
            source_id="opaque_projection",
        )
        base = next(
            item
            for item in extracted
            if item.outcome == "bounded_semantic_opaque"
        )
        obligation_count = 1_001
        obligations = [
            replace(
                base,
                evidence_id=f"ev_opaque_{index:04d}",
                source_id=f"opaque_{index:04d}",
                source_entity_id=f"automation.opaque_{index:04d}",
                expression_fingerprint=f"{index:064x}",
            )
            for index in range(obligation_count)
        ]
        dynamic = [
            DynamicReference(
                evidence_id=f"dyn_opaque_{index:04d}",
                source_type="automation",
                source_id=f"opaque_{index:04d}",
                source_entity_id=f"automation.opaque_{index:04d}",
                config_path="$.condition[0].value_template",
                warning="Unsupported dynamic entity lookup.",
            )
            for index in range(obligation_count)
        ]
        result = DependencyScanResult(
            findings=[],
            dynamic_references=dynamic,
            target_metadata={},
            coverage=_coverage(),
            obligations=obligations,
            obligation_ledger_model=OBLIGATION_LEDGER_MODEL,
            home_assistant_version=SUPPORTED_HA_VERSION,
            home_assistant_version_status="observed",
        )

        index = DependencyIndex(_FakeProvider(result))
        snapshot, _rebuilt, _lookup_ms = await index.get(refresh=True)

        self.assertEqual(len(snapshot.dynamic_references), 1_000)
        self.assertEqual(snapshot.dynamic_reference_overflow_count, 1)
        self.assertEqual(len(snapshot.obligations), obligation_count)
        self.assertEqual(snapshot.obligation_overflow_count, 0)
        self.assertEqual(
            len(snapshot.obligation_diagnostics), obligation_count
        )
        self.assertEqual(
            snapshot.obligation_diagnostic_overflow_count, 0
        )
        coverage = next(
            item
            for item in snapshot.coverage
            if item.source_type == "automation"
        )
        self.assertEqual(coverage.completeness, "partial")
        self.assertEqual(
            coverage.obligation_ledger_completeness, "partial"
        )
        self.assertEqual(
            coverage.failed_item_count, obligation_count
        )
        self.assertEqual(
            coverage.obligation_ledger_failed_item_count,
            obligation_count,
        )
        public = coverage.public()
        self.assertLessEqual(len(public["warnings"]), 10)
        self.assertEqual(
            public["obligation_ledger_failed_item_count"],
            obligation_count,
        )
        binding = build_helper_dependency_risk_binding(
            snapshot,
            entity_id=TARGET,
            index_metadata=index.evidence_metadata(snapshot),
        )
        self.assertFalse(binding["coverage_complete"])
        self.assertFalse(binding["execution_eligible"])

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
