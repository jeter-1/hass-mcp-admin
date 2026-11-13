"""Adversarial tests for the bounded F3-B JSON Pointer compiler."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hass_mcp_engineering_beta"))
sys.path.insert(0, str(Path(__file__).parent))

from ha_mcp_engineering.f3_dashboard.constants import (  # noqa: E402
    MAX_INDIVIDUAL_VALUE_BYTES,
    MAX_PATCH_OPERATIONS,
    MAX_POINTER_DEPTH,
)
from ha_mcp_engineering.f3_dashboard.errors import (  # noqa: E402
    COMPILER_LIMIT_DIAGNOSTICS,
    ApprovalProjectionError,
    DashboardFoundationError,
    PatchCompilationError,
    PatchValidationError,
    RawEvidenceError,
    is_compiler_limit_refusal,
    json_bound_error,
    json_bound_of,
)
from ha_mcp_engineering.f3_dashboard.json_codec import (  # noqa: E402
    canonical_json_bytes,
    engineering_sha256,
    strict_json_equal,
    validate_json_value,
)
from ha_mcp_engineering.f3_dashboard.patch import (  # noqa: E402
    _leaf_weight,
    compile_dashboard_patch,
    parse_pointer,
    semantic_leaf_difference,
)
from f3_dashboard_support import (  # noqa: E402
    PRIVATE_KEY,
    PRIVATE_TEXT,
    compiler_limit_cases,
    home_dashboard_patch_operations,
    load_dashboard,
    load_home_dashboard,
)


def operation(kind: str, path: str, value=..., operation_id: str = "change"):
    result = {"operation_id": operation_id, "operation": kind, "path": path}
    if value is not ...:
        result["value"] = value
    return result


class DashboardPatchCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_dashboard()

    def compile(self, *operations):
        return compile_dashboard_patch(self.config, list(operations))

    def test_valid_mapping_add_replace_and_remove(self):
        added = self.compile(operation("add", "/new_field", False))
        self.assertIs(added.resulting_configuration["new_field"], False)
        replaced = self.compile(operation("replace", "/title", "Changed"))
        self.assertEqual(replaced.resulting_configuration["title"], "Changed")
        removed = self.compile(operation("remove", "/unknown_root_extension"))
        self.assertNotIn("unknown_root_extension", removed.resulting_configuration)

    def test_compiler_produces_no_python_setter_realization(self):
        compiled = self.compile(operation("replace", "/title", "Changed"))
        self.assertFalse(hasattr(compiled, "generated_transform"))
        self.assertFalse(hasattr(compiled, "generated_transform_model"))
        self.assertFalse(hasattr(compiled, "generated_transform_sha256"))

    def test_valid_existing_list_replace_and_remove(self):
        replaced = self.compile(
            operation(
                "replace",
                "/views/0/badges/0",
                "sensor.synthetic_replacement",
            )
        )
        self.assertEqual(
            replaced.resulting_configuration["views"][0]["badges"][0],
            "sensor.synthetic_replacement",
        )
        removed = self.compile(operation("remove", "/views/0/badges/0"))
        self.assertIsInstance(removed.resulting_configuration["views"][0]["badges"][0], dict)

    def test_canonical_pointer_escaping_is_decoded_exactly_once(self):
        compiled = self.compile(
            operation("add", "/unknown_root_extension/nested/a~1b", 1)
        )
        self.assertEqual(
            compiled.resulting_configuration["unknown_root_extension"]["nested"]["a/b"],
            1,
        )
        self.assertEqual(parse_pointer("/a~01"), ("a~1",))
        with self.assertRaises(PatchValidationError):
            parse_pointer("/a~2b")

    def test_root_wildcard_predicate_and_fuzzy_forms_are_rejected(self):
        invalid = ("", "/views/*", "/views/[0]", "/views/?title=x", "/views/..", "/views/-")
        for path in invalid:
            with self.subTest(path=path), self.assertRaises(PatchValidationError):
                parse_pointer(path)

    def test_negative_leading_zero_non_numeric_and_out_of_range_indices_rejected(self):
        paths = (
            "/views/-1/title",
            "/views/01/title",
            "/views/one/title",
            "/views/99/title",
        )
        for path in paths:
            with self.subTest(path=path), self.assertRaises(PatchCompilationError):
                self.compile(operation("replace", path, "x"))

    def test_mapping_and_missing_target_semantics_fail_closed(self):
        cases = (
            operation("replace", "/missing", 1),
            operation("add", "/title", "duplicate"),
            operation("remove", "/missing"),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(
                (PatchCompilationError, PatchValidationError)
            ):
                self.compile(candidate)

    def test_array_add_supports_append_and_canonical_insertion(self):
        config = {"items": ["a", "b", "c"]}
        cases = (
            ("/items/-", ["a", "b", "c", "x"]),
            ("/items/0", ["x", "a", "b", "c"]),
            ("/items/1", ["a", "x", "b", "c"]),
            ("/items/3", ["a", "b", "c", "x"]),
        )
        for path, expected in cases:
            with self.subTest(path=path):
                original = deepcopy(config)
                compiled = compile_dashboard_patch(
                    config, [operation("add", path, "x")]
                )
                self.assertEqual(compiled.resulting_configuration["items"], expected)
                self.assertEqual(config, original)
                effect = compiled.effects[0]
                if path == "/items/0":
                    self.assertTrue(effect.previous_present)
                    self.assertEqual(effect.previous_value, ["a", "b", "c"])
                    self.assertEqual(effect.proposed_value, ["x", "a", "b", "c"])
                elif path == "/items/1":
                    self.assertTrue(effect.previous_present)
                    self.assertEqual(effect.previous_value, ["b", "c"])
                    self.assertEqual(effect.proposed_value, ["x", "b", "c"])
                else:
                    self.assertFalse(effect.previous_present)
                    self.assertIsNone(effect.previous_value)
                    self.assertEqual(effect.proposed_value, "x")
        self.assertEqual(
            parse_pointer("/items/-", operation="add"), ("items", "-")
        )

    def test_invalid_array_add_and_append_forms_fail_closed(self):
        cases = (
            ({"items": ["a"]}, operation("add", "/items/2", "x")),
            ({"items": ["a"]}, operation("add", "/items/-1", "x")),
            ({"items": ["a"]}, operation("add", "/items/one", "x")),
            ({"items": ["a"]}, operation("add", "/items/01", "x")),
            ({"items": [["a"]]}, operation("add", "/items/-/0", "x")),
            ({"items": ["a"]}, operation("replace", "/items/-", "x")),
            ({"items": ["a"]}, operation("remove", "/items/-")),
            ({"items": {}}, operation("add", "/items/-", "x")),
            ({"items": ["a"]}, operation("add", "/items/key", "x")),
        )
        for config, candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(
                (PatchCompilationError, PatchValidationError)
            ):
                compile_dashboard_patch(config, [candidate])

    def test_array_remove_projects_complete_shifted_suffix(self):
        config = {"items": ["remove", {"nested": [1, 2]}, "tail"]}

        compiled = compile_dashboard_patch(
            config, [operation("remove", "/items/0")]
        )
        effect = compiled.effects[0]

        self.assertEqual(
            compiled.resulting_configuration["items"],
            [{"nested": [1, 2]}, "tail"],
        )
        self.assertTrue(effect.previous_present)
        self.assertEqual(
            effect.previous_value,
            ["remove", {"nested": [1, 2]}, "tail"],
        )
        self.assertTrue(effect.proposed_present)
        self.assertEqual(
            effect.proposed_value,
            [{"nested": [1, 2]}, "tail"],
        )
        self.assertEqual(effect.leaf_change_count, 6)

    def test_shifted_array_suffix_counts_against_semantic_bound(self):
        within_bound = {"items": list(range(7))}
        compiled = compile_dashboard_patch(
            within_bound, [operation("add", "/items/0", -1)]
        )
        self.assertEqual(compiled.semantic_leaf_change_count, 9)

        beyond_bound = {"items": list(range(256))}
        for candidate in (
            operation("add", "/items/0", -1),
            operation("remove", "/items/0"),
        ):
            with self.subTest(operation=candidate["operation"]), self.assertRaisesRegex(
                PatchCompilationError, "semantic leaf change limit"
            ):
                compile_dashboard_patch(beyond_bound, [candidate])

    def test_array_append_growth_counts_against_semantic_bound(self):
        single = compile_dashboard_patch(
            {"items": []}, [operation("add", "/items/0", 0)]
        )
        self.assertEqual(single.semantic_leaf_change_count, 2)

        sixteen_appends = [
            operation("add", f"/items/{index}", index, f"append-{index}")
            for index in range(16)
        ]
        compiled = compile_dashboard_patch({"items": []}, sixteen_appends)
        self.assertEqual(compiled.semantic_leaf_change_count, 32)
        self.assertEqual(compiled.resulting_configuration["items"], list(range(16)))
        larger_appends = [
            operation("add", f"/items/{index}", list(range(16)), f"append-{index}")
            for index in range(16)
        ]
        with self.assertRaises(PatchCompilationError) as caught:
            compile_dashboard_patch({"items": []}, larger_appends)
        self.assertEqual(caught.exception.constraint, "semantic_leaf_changes")
        self.assertGreater(caught.exception.observed, 256)

    def test_duplicate_alias_and_parent_child_paths_are_rejected(self):
        with self.assertRaises(PatchValidationError):
            self.compile(
                operation("replace", "/title", "a", "first"),
                operation("replace", "/title", "b", "second"),
            )
        with self.assertRaises(PatchValidationError):
            self.compile(
                operation("replace", "/unknown_root_extension", {}, "parent"),
                operation("replace", "/unknown_root_extension/nested", {}, "child"),
            )

    def test_operations_are_applied_in_declared_order(self):
        config = {"items": ["a", "b", "c"]}
        first = compile_dashboard_patch(
            config,
            [operation("remove", "/items/0", operation_id="first"), operation("remove", "/items/1", operation_id="second")],
        )
        second = compile_dashboard_patch(
            config,
            [operation("remove", "/items/1", operation_id="second"), operation("remove", "/items/0", operation_id="first")],
        )
        self.assertEqual(first.resulting_configuration, {"items": ["b"]})
        self.assertEqual(second.resulting_configuration, {"items": ["c"]})

    def test_operation_count_and_pointer_depth_are_bounded(self):
        too_many = [operation("add", f"/field_{index}", index, f"op_{index}") for index in range(MAX_PATCH_OPERATIONS + 1)]
        with self.assertRaises(PatchValidationError):
            compile_dashboard_patch({}, too_many)
        deep_path = "/" + "/".join("x" for _ in range(MAX_POINTER_DEPTH + 1))
        with self.assertRaises(PatchValidationError):
            parse_pointer(deep_path)

    def test_oversized_value_patch_and_result_are_rejected(self):
        with self.assertRaises(PatchValidationError):
            compile_dashboard_patch({}, [operation("add", "/large", "x" * (MAX_INDIVIDUAL_VALUE_BYTES + 1))])
        operations = [
            operation("add", f"/value_{index}", "x" * 1200, f"add_{index}")
            for index in range(16)
        ]
        with self.assertRaises(PatchValidationError):
            compile_dashboard_patch({}, operations)
        with self.assertRaises(PatchCompilationError):
            compile_dashboard_patch(
                {"padding": "x" * 39_980, "value": 1},
                [operation("replace", "/value", "y" * 100)],
            )

    def test_nonfinite_executable_and_unknown_operation_fields_are_rejected(self):
        for value in (math.nan, math.inf, -math.inf, lambda: None, object()):
            with self.subTest(value=type(value).__name__), self.assertRaises(PatchValidationError):
                self.compile(operation("replace", "/title", value))
        candidate = operation("replace", "/title", "x")
        candidate["python_transform"] = "danger()"
        with self.assertRaises(PatchValidationError):
            self.compile(candidate)

    def test_missing_or_extra_values_and_unsupported_operations_are_rejected(self):
        cases = (
            operation("replace", "/title"),
            operation("add", "/new"),
            operation("remove", "/title", None),
            operation("move", "/title", "x"),
            operation("copy", "/title", "x"),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(PatchValidationError):
                self.compile(candidate)

    def test_broad_subtree_replacement_cannot_bypass_leaf_bound(self):
        replacement = {f"leaf_{index}": index + 1 for index in range(257)}
        with self.assertRaisesRegex(PatchCompilationError, "semantic leaf change limit"):
            compile_dashboard_patch(
                {"views": {f"leaf_{index}": index for index in range(257)}},
                [operation("replace", "/views", replacement)],
            )
        self.assertEqual(
            semantic_leaf_difference({"a": 1, "b": 2}, {"b": 2, "a": 1}),
            0,
        )

    def test_strict_json_equality_distinguishes_boolean_integer_and_float(self):
        cases = ((True, 1), (False, 0), (1, 1.0))
        for before, after in cases:
            with self.subTest(before=before, after=after):
                self.assertFalse(strict_json_equal(before, after))
                self.assertEqual(semantic_leaf_difference(before, after), 1)
                self.assertNotEqual(
                    engineering_sha256(before), engineering_sha256(after)
                )
        self.assertTrue(
            strict_json_equal(
                {"a": [True, 1, 1.0]}, {"a": [True, 1, 1.0]}
            )
        )
        self.assertTrue(strict_json_equal({"a": 1, "b": 2}, {"b": 2, "a": 1}))

    def test_nested_typed_changes_are_counted(self):
        self.assertEqual(
            semantic_leaf_difference(
                {"nested": [True, {"value": 1}]},
                {"nested": [1, {"value": 1.0}]},
            ),
            2,
        )

    def test_257_boolean_number_changes_exceed_leaf_bound(self):
        for before_value, after_value in ((True, 1), (False, 0)):
            before = {
                "values": {
                    f"item_{index}": before_value for index in range(257)
                }
            }
            after = {
                f"item_{index}": after_value for index in range(257)
            }
            with self.subTest(before_value=before_value), self.assertRaisesRegex(
                PatchCompilationError, "semantic leaf change limit"
            ):
                compile_dashboard_patch(
                    before,
                    [operation("replace", "/values", after)],
                )

    def test_exact_semantic_limit_and_counted_diagnostics(self):
        for count in (255, 256, 257, 300):
            before = {"values": {f"k{i}": 0 for i in range(count)}}
            after = {f"k{i}": 1 for i in range(count)}
            with self.subTest(count=count):
                if count <= 256:
                    compiled = compile_dashboard_patch(
                        before, [operation("replace", "/values", after)]
                    )
                    self.assertEqual(compiled.semantic_leaf_change_count, count)
                    self.assertEqual(compiled.resulting_configuration, {"values": after})
                else:
                    with self.assertRaises(PatchCompilationError) as caught:
                        compile_dashboard_patch(
                            before, [operation("replace", "/values", after)]
                        )
                    self.assertEqual(caught.exception.diagnostic_details(), {
                        "reason": "dashboard_patch_compilation_failed",
                        "dashboard_error_code": "dashboard_patch_compilation_failed",
                        "constraint": "semantic_leaf_changes",
                        "stage": "compilation",
                        "observed": count,
                        "limit": 256,
                    })
                self.assertEqual(before["values"], {f"k{i}": 0 for i in range(count)})

    def test_original_home_fixture_provenance_and_current_suffix_accounting(self):
        directory = ROOT / "tests" / "fixtures" / "f3_dashboard"
        provenance = json.loads((directory / "home_dashboard_provenance.json").read_text())
        for name, evidence in (
            ("home_dashboard_existing.json", provenance["configuration"]),
            ("home_dashboard_patch.json", provenance["operations"]),
        ):
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(),
                             evidence["sha256"])
        compiled = compile_dashboard_patch(
            load_home_dashboard(), home_dashboard_patch_operations()
        )
        self.assertEqual(compiled.semantic_leaf_change_count, 54)
        self.assertEqual([effect.leaf_change_count for effect in compiled.effects],
                         [8, 2, 4, 40])

    def test_unknown_fields_falsey_values_and_order_are_preserved(self):
        original = deepcopy(self.config)
        compiled = self.compile(operation("replace", "/title", "Changed"))
        result = compiled.resulting_configuration
        self.assertEqual(result["unknown_root_extension"], original["unknown_root_extension"])
        future = result["views"][0]["sections"][0]["cards"][0]["unknown_future_field"]
        self.assertEqual(
            future,
            {
                "keep_null": None,
                "keep_false": False,
                "keep_zero": 0,
                "keep_empty_string": "",
                "keep_empty_list": [],
                "keep_empty_object": {},
            },
        )
        self.assertEqual(result["unknown_root_extension"]["ordered_list"], ["first", "second", "third"])

    def test_large_bounded_list_heavy_configuration_preserves_unknown_fields(self):
        config = {
            "views": [
                {
                    "title": "Large synthetic view",
                    "cards": [
                        {
                            "type": "custom:synthetic-large-card",
                            "entity": f"sensor.synthetic_{index}",
                            "future": {"index": index, "false": False, "null": None},
                        }
                        for index in range(200)
                    ],
                }
            ]
        }
        original_cards = deepcopy(config["views"][0]["cards"])
        compiled = compile_dashboard_patch(
            config,
            [
                {
                    "operation_id": "add-title",
                    "operation": "add",
                    "path": "/title",
                    "value": "Bounded result",
                }
            ],
        )
        self.assertEqual(compiled.resulting_configuration["views"][0]["cards"], original_cards)
        self.assertLess(compiled.resulting_size_bytes, 40_000)

    def test_compile_and_hashing_are_deterministic_and_input_is_unchanged(self):
        original = deepcopy(self.config)
        candidate = operation("replace", "/title", "Deterministic")
        first = self.compile(candidate)
        second = self.compile(candidate)
        self.assertEqual(first, second)
        self.assertEqual(self.config, original)
        self.assertEqual(
            canonical_json_bytes(first.resulting_configuration),
            canonical_json_bytes(second.resulting_configuration),
        )
        self.assertRegex(first.preread_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(first.resulting_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(first.resulting_upstream_config_hash, r"^[0-9a-f]{16}$")


class CompilerLimitDiagnosticTests(unittest.TestCase):
    """HAMCP-143: each compiler limit names itself with bounded numbers only."""

    def test_every_limit_refusal_names_its_constraint_at_limit_plus_one(self):
        cases = compiler_limit_cases()
        self.assertEqual(
            {(case.constraint, case.stage) for case in cases}
            | {("semantic_leaf_changes", "compilation"), ("traversal_depth", "compilation")},
            set(COMPILER_LIMIT_DIAGNOSTICS),
        )
        for case in cases:
            with self.subTest(constraint=case.constraint):
                configuration = deepcopy(case.configuration)
                operations = deepcopy(case.over_operations)
                error_class = (
                    PatchValidationError
                    if case.code == "invalid_dashboard_patch"
                    else PatchCompilationError
                )
                with self.assertRaises(error_class) as caught:
                    compile_dashboard_patch(configuration, operations)
                self.assertIs(type(caught.exception), error_class)
                self.assertEqual(caught.exception.code, case.code)
                details = caught.exception.diagnostic_details()
                self.assertEqual(details, case.expected_details())
                self.assertTrue(is_compiler_limit_refusal(caught.exception))
                self.assertTrue(
                    all(
                        isinstance(item, int) and not isinstance(item, bool)
                        for key, item in details.items()
                        if key in {"observed", "limit"}
                    )
                )
                rendered = json.dumps(details)
                self.assertNotIn(PRIVATE_KEY, rendered)
                self.assertNotIn(PRIVATE_TEXT, rendered)
                self.assertNotIn("synthetic", rendered)
                self.assertNotIn("/", rendered)
                self.assertEqual(configuration, case.configuration)
                self.assertEqual(operations, case.over_operations)

    def test_every_limit_accepts_its_boundary_value(self):
        for case in compiler_limit_cases():
            with self.subTest(constraint=case.constraint):
                if case.at_limit_refusal is None:
                    compiled = compile_dashboard_patch(
                        deepcopy(case.at_limit_config), deepcopy(case.at_limit_operations)
                    )
                    self.assertLessEqual(compiled.semantic_leaf_change_count, 256)
                    continue
                with self.assertRaises(DashboardFoundationError) as caught:
                    compile_dashboard_patch(
                        deepcopy(case.at_limit_config), deepcopy(case.at_limit_operations)
                    )
                # At its own limit the tested bound holds; only another
                # reviewed bound that the shape necessarily crosses refuses.
                self.assertEqual(caught.exception.constraint, case.at_limit_refusal)
                self.assertNotEqual(caught.exception.constraint, case.constraint)
                self.assertTrue(is_compiler_limit_refusal(caught.exception))

    def test_traversal_depth_bound_is_named_without_an_untruthful_count(self):
        # Shared JSON validation refuses deeper public input first, so the
        # traversal depth bound is defence in depth and proven directly.
        self.assertEqual(semantic_leaf_difference(0, 1, depth=48), 1)
        self.assertEqual(_leaf_weight(0, depth=48), 1)
        for call in (
            lambda: semantic_leaf_difference(0, 1, depth=49),
            lambda: _leaf_weight(0, depth=49),
        ):
            with self.assertRaises(PatchCompilationError) as caught:
                call()
            self.assertEqual(caught.exception.diagnostic_details(), {
                "reason": "dashboard_patch_compilation_failed",
                "dashboard_error_code": "dashboard_patch_compilation_failed",
                "constraint": "traversal_depth",
                "stage": "compilation",
                "limit": 48,
            })
            self.assertTrue(is_compiler_limit_refusal(caught.exception))

    def test_non_limit_failures_keep_legacy_details(self):
        cases = (
            ({}, []),
            ({}, [operation("add", "/x", 1, "dup"), operation("add", "/y", 1, "dup")]),
            ({}, [{"operation_id": "x", "operation": "add", "path": "/x", "value": 1, "extra": 1}]),
            ({}, [operation("replace", "/missing", 1)]),
            ({}, [operation("add", "/x", math.nan)]),
            ({"items": [1]}, [operation("add", "/items/9", 1)]),
        )
        for configuration, operations in cases:
            with self.subTest(operations=operations), self.assertRaises(
                DashboardFoundationError
            ) as caught:
                compile_dashboard_patch(configuration, operations)
            self.assertIsNone(caught.exception.constraint)
            self.assertFalse(is_compiler_limit_refusal(caught.exception))
            self.assertEqual(
                caught.exception.diagnostic_details(),
                {
                    "reason": caught.exception.code,
                    "dashboard_error_code": caught.exception.code,
                },
            )

    def test_shared_json_bounds_outside_the_compiler_stay_legacy(self):
        for value in ([0] * 10_000, json.loads("[" * 49 + "0" + "]" * 49)):
            with self.subTest(size=len(json.dumps(value))), self.assertRaises(
                PatchValidationError
            ) as caught:
                validate_json_value(value)
            # Class identity is persisted as a failure category elsewhere.
            self.assertIs(type(caught.exception), PatchValidationError)
            self.assertIsNotNone(json_bound_of(caught.exception))
            self.assertIsNone(caught.exception.constraint)
            self.assertFalse(is_compiler_limit_refusal(caught.exception))
            self.assertEqual(caught.exception.diagnostic_details(), {
                "reason": "invalid_dashboard_patch",
                "dashboard_error_code": "invalid_dashboard_patch",
            })

    def test_json_bound_tag_is_closed_and_bounded(self):
        self.assertEqual(
            json_bound_of(json_bound_error("JSON node limit exceeded", bound="nodes", limit=5)),
            ("nodes", 5),
        )
        for bound, limit in (("width", 1), ("nodes", -1), ("depth", True), ("depth", "1")):
            with self.subTest(bound=bound, limit=limit), self.assertRaises(ValueError):
                json_bound_error("bounded", bound=bound, limit=limit)
        forged = PatchValidationError("forged")
        for tag in (("width", 1), ("nodes", "1"), ("nodes",), ["nodes", 1], None):
            forged.json_bound = tag
            with self.subTest(tag=tag):
                self.assertIsNone(json_bound_of(forged))
        compiled_error = PatchCompilationError("other")
        compiled_error.json_bound = ("nodes", 1)
        self.assertIsNone(json_bound_of(compiled_error))
        self.assertIsNone(json_bound_of(ValueError("plain")))

    def test_only_reviewed_compiler_limit_pairs_are_public(self):
        refused = (
            PatchCompilationError(constraint="semantic_leaf_changes", stage="validation", limit=1),
            PatchValidationError(constraint="operation_count", stage="compilation", limit=1),
            PatchValidationError(constraint="operation_count", stage="validation"),
            PatchValidationError(constraint="unreviewed_bound", stage="validation", limit=1),
            RawEvidenceError(constraint="result_config_bytes", stage="compilation", limit=1),
            ApprovalProjectionError(constraint="operation_count", stage="validation", limit=1),
            ValueError("plain"),
        )
        for exc in refused:
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(is_compiler_limit_refusal(exc))
        self.assertTrue(is_compiler_limit_refusal(
            PatchCompilationError(constraint="semantic_leaf_changes", stage="compilation", limit=256)
        ))


if __name__ == "__main__":
    unittest.main()
