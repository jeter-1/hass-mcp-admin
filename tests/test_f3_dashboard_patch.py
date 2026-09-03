"""Adversarial tests for the bounded F3-B JSON Pointer compiler."""

from __future__ import annotations

from copy import deepcopy
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
    MAX_SEMANTIC_LEAF_CHANGES,
)
from ha_mcp_engineering.f3_dashboard.errors import PatchCompilationError, PatchValidationError  # noqa: E402
from ha_mcp_engineering.f3_dashboard.json_codec import (  # noqa: E402
    canonical_json_bytes,
    engineering_sha256,
    strict_json_equal,
)
from ha_mcp_engineering.f3_dashboard.patch import (  # noqa: E402
    compile_dashboard_patch,
    parse_pointer,
    semantic_leaf_difference,
)
from f3_dashboard_support import (  # noqa: E402
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
                    config,
                    [operation("add", path, "x")],
                )
                self.assertEqual(
                    compiled.resulting_configuration["items"], expected
                )
                self.assertEqual(config, original)
        self.assertEqual(
            parse_pointer("/items/-", operation="add"),
            ("items", "-"),
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
        with self.assertRaises(PatchValidationError) as oversized_value:
            compile_dashboard_patch({}, [operation("add", "/large", "x" * (MAX_INDIVIDUAL_VALUE_BYTES + 1))])
        self.assertEqual(oversized_value.exception.reason, "patch_value_too_large")
        self.assertEqual(
            oversized_value.exception.constraint, "individual_value_bytes"
        )
        self.assertEqual(
            oversized_value.exception.limit, MAX_INDIVIDUAL_VALUE_BYTES
        )
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

    def test_whole_view_failure_reports_the_exact_preserved_material_bound(self):
        dashboard = load_dashboard()
        replacement = deepcopy(dashboard["views"][0])
        replacement["sections"].append(
            {
                "cards": [
                    {"type": "markdown", "content": "x" * 8200}
                ]
            }
        )
        with self.assertRaises(PatchValidationError) as caught:
            compile_dashboard_patch(
                dashboard,
                [operation("replace", "/views/0", replacement)],
            )
        self.assertEqual(caught.exception.reason, "patch_value_too_large")
        self.assertEqual(caught.exception.constraint, "individual_value_bytes")
        self.assertEqual(caught.exception.observed, 9537)
        self.assertEqual(caught.exception.limit, 8192)
        self.assertEqual(caught.exception.stage, "validation")

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
        replacement = {
            f"leaf_{index}": index + 1
            for index in range(MAX_SEMANTIC_LEAF_CHANGES + 1)
        }
        with self.assertRaises(PatchCompilationError) as caught:
            compile_dashboard_patch(
                {
                    "views": {
                        f"leaf_{index}": index
                        for index in range(MAX_SEMANTIC_LEAF_CHANGES + 1)
                    }
                },
                [operation("replace", "/views", replacement)],
            )
        self.assertEqual(caught.exception.reason, "dashboard_patch_limit_exceeded")
        self.assertEqual(caught.exception.constraint, "semantic_leaf_changes")
        self.assertEqual(
            caught.exception.observed, MAX_SEMANTIC_LEAF_CHANGES + 1
        )
        self.assertEqual(caught.exception.limit, MAX_SEMANTIC_LEAF_CHANGES)
        self.assertEqual(caught.exception.stage, "compilation")
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

    def test_semantic_leaf_complexity_bound_is_256_not_review_sufficiency(self):
        for count in (16, 17, MAX_SEMANTIC_LEAF_CHANGES):
            with self.subTest(count=count):
                before = {
                    "values": {
                        f"item_{index}": False for index in range(count)
                    }
                }
                after = {
                    f"item_{index}": True for index in range(count)
                }
                compiled = compile_dashboard_patch(
                    before,
                    [operation("replace", "/values", after)],
                )
                self.assertEqual(compiled.semantic_leaf_change_count, count)

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

    def test_realistic_home_dashboard_delta_compiles_as_one_bounded_patch(self):
        baseline = load_home_dashboard()
        original = deepcopy(baseline)
        compiled = compile_dashboard_patch(
            baseline, home_dashboard_patch_operations()
        )
        result = compiled.resulting_configuration

        status_chips = result["views"][0]["sections"][0]["cards"][1][
            "chips"
        ]
        self.assertEqual(
            [chip["content"] for chip in status_chips],
            ["Home", "Guest", "Cleaner", "Sleep"],
        )
        climate_chips = result["views"][0]["sections"][1]["cards"][1][
            "chips"
        ]
        self.assertEqual(
            [chip["content"] for chip in climate_chips],
            ["Mode", "Season", "Outdoor"],
        )
        self.assertEqual(
            climate_chips[2]["entity"],
            "sensor.local_outdoor_temperature",
        )
        attention = result["views"][0]["sections"][0]["cards"][-1]
        self.assertEqual(attention["title"], "Needs Attention")
        self.assertEqual(attention["cards"][1]["type"], "conditional")
        conditions = attention["cards"][1]["conditions"][0]["conditions"]
        self.assertEqual(len(conditions), 7)
        self.assertEqual(
            result["views"][0]["sections"][2],
            original["views"][0]["sections"][2],
        )
        self.assertEqual(result["strategy"], original["strategy"])
        self.assertEqual(baseline, original)
        self.assertEqual(compiled.semantic_leaf_change_count, 51)
        self.assertEqual(compiled.serialized_patch_bytes, 1658)
        self.assertEqual(compiled.resulting_size_bytes, 2345)
        self.assertEqual(compiled.configuration_growth_bytes, 1057)
        self.assertEqual(
            compiled.resulting_sha256,
            "4c3b81d8fff6e2d54754a5e87f90f4972b4e4fb8e8c99e2144d0f9180611e466",
        )

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


if __name__ == "__main__":
    unittest.main()
