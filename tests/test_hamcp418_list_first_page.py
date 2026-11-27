"""HAMCP-418 (E048): an oversize list returns a first page of whole rows.

The general response bound removes the largest optional subtree first. For a
list response that subtree is the list, so an oversize list returned zero
rows. List responses now return whole leading rows in upstream order with
explicit counts; non-list projection is unchanged.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.models import responses
from ha_mcp_engineering.models.responses import MAX_CHARS, SuccessResponse, dump_json_with_truncation


def rows(count=2000):
    return [{"entity_id": f"automation.synthetic_{index:04d}", "state": "on",
             "attributes": {"friendly_name": f"Synthetic {index}", "note": "x" * 40}}
            for index in range(count)]


def envelope(data, operation="ha_config_list_automations"):
    return SuccessResponse(operation=operation, summary="synthetic", data=data,
                           metadata={"routing": {"provider": "ha_mcp", "fallback_occurred": False}},
                           request_id="req-synthetic").as_dict()


def general(value, limit=MAX_CHARS):
    """Today's projection: the same bound with list paging disabled."""
    with patch.object(responses, "_list_first_page", return_value=None):
        return dump_json_with_truncation(value, limit)


class ListFirstPageTests(unittest.TestCase):
    def render(self, value, limit=MAX_CHARS):
        original = deepcopy(value)
        rendered, truncated = dump_json_with_truncation(value, limit)
        self.assertEqual(value, original, "projection must not mutate its input")
        self.assertLessEqual(len(rendered.encode()), limit)
        return json.loads(rendered), truncated

    def assert_first_page(self, result, page, source, pointer, limit=MAX_CHARS):
        completeness = result["response_completeness"]
        returned = completeness["returned_count"]
        self.assertTrue(completeness["truncated"])
        self.assertEqual(completeness["completeness"], "partial")
        self.assertEqual(completeness["projection"], "list_first_page")
        self.assertEqual(completeness["list_path"], pointer)
        self.assertEqual(completeness["reason"], "response_size_limit")
        self.assertGreater(returned, 0)
        self.assertEqual(returned + completeness["omitted_count"], len(source))
        self.assertGreater(completeness["omitted_count"], 0)
        # Whole rows, upstream order, a prefix of what Engineering received.
        self.assertEqual(page, source[:returned])
        notice = completeness["notice"]
        self.assertTrue(notice.startswith(f"... [truncated at {limit} chars]"))
        self.assertIn(f"first {returned} of {len(source)} rows", notice)
        self.assertIn("narrow the query", notice)
        self.assertNotIn("omitted_paths", completeness)
        return completeness

    def test_2000_rows_nested_under_data_return_a_first_page(self):
        source = rows()
        value = envelope({"automations": source, "count": len(source), "filter": "all"})
        result, truncated = self.render(value)
        self.assertTrue(truncated)
        completeness = self.assert_first_page(
            result, result["data"]["automations"], source, "/data/automations")
        self.assertNotIn("total_count", completeness)
        # HAMCP-432: a count equal to the rows received becomes the rows kept.
        self.assertEqual(result["data"]["count"], completeness["returned_count"])
        # Other siblings, metadata and attribution are kept unchanged.
        self.assertEqual(result["data"]["filter"], "all")
        self.assertEqual(result["metadata"], value["metadata"])
        self.assertEqual(result["operation"], value["operation"])
        self.assertTrue(result["success"])
        # The page uses most of the bound rather than a token row count.
        self.assertGreater(len(json.dumps(result, separators=(",", ":"))), MAX_CHARS - 200)

    def test_general_projection_returned_zero_nested_rows(self):
        # E048 reproduction: without paging the whole nested list is removed.
        value = envelope({"automations": rows(), "count": 2000})
        result = json.loads(general(value)[0])
        self.assertNotIn("automations", result.get("data", {}))
        self.assertIn("/data", result["response_completeness"]["omitted_paths"][0])

    def test_2000_rows_top_level_return_a_first_page(self):
        source = rows()
        result, truncated = self.render(envelope(source))
        self.assertTrue(truncated)
        completeness = self.assert_first_page(result, result["data"], source, "/data")
        self.assertNotIn("total_count", completeness)

    def test_bare_top_level_list_returns_a_first_page(self):
        source = rows()
        result, truncated = self.render(source)
        self.assertTrue(truncated)
        self.assert_first_page(result, result["data"], source, "/data")

    def test_nested_and_top_level_lists_return_the_same_rows(self):
        source = rows()
        nested, _ = self.render(envelope({"automations": source}))
        top, _ = self.render(envelope(source))
        returned = nested["response_completeness"]["returned_count"]
        # The nested key costs a few characters; the rule is the same.
        self.assertLessEqual(abs(returned - top["response_completeness"]["returned_count"]), 1)
        self.assertEqual(nested["data"]["automations"], source[:returned])

    def test_declared_total_count_is_reported(self):
        source = rows()
        result, _ = self.render(envelope({"automations": source, "total_count": 5000}))
        self.assertEqual(result["response_completeness"]["total_count"], 5000)
        self.assertEqual(result["data"]["total_count"], 5000)

    def test_inconsistent_declared_total_is_not_reported(self):
        source = rows()
        for declared in (10, "2000", True, None):
            with self.subTest(declared=declared):
                result, _ = self.render(envelope({"automations": source, "total_count": declared}))
                self.assertNotIn("total_count", result["response_completeness"])

    def test_largest_list_is_paged_and_other_lists_kept(self):
        source = rows()
        warnings = ["synthetic warning"]
        result, _ = self.render(envelope({"warnings": warnings, "automations": source}))
        self.assertEqual(result["data"]["warnings"], warnings)
        self.assert_first_page(result, result["data"]["automations"], source, "/data/automations")

    def test_one_oversize_row_keeps_the_general_bounded_projection(self):
        source = [{"entity_id": "automation.huge", "state": "on", "note": "y" * 70_000},
                  {"entity_id": "automation.small", "state": "off"}]
        for data, pointer in ((source, "/data"), ({"automations": source}, "/data/automations")):
            with self.subTest(pointer=pointer):
                value = envelope(data)
                result, truncated = self.render(value)
                self.assertTrue(truncated)
                completeness = result["response_completeness"]
                self.assertTrue(completeness["truncated"])
                self.assertNotEqual(completeness.get("projection"), "list_first_page")
                self.assertNotIn("returned_count", completeness)
                self.assertGreater(completeness["omitted_path_count"], 0)
                # Equal to today's general projection, byte for byte.
                self.assertEqual(dump_json_with_truncation(value), general(value))

    def test_small_list_responses_are_unchanged(self):
        for data in (rows(5), {"automations": rows(5), "count": 5}):
            with self.subTest(kind=type(data).__name__):
                value = envelope(data)
                rendered, truncated = dump_json_with_truncation(value)
                self.assertFalse(truncated)
                self.assertEqual(rendered, json.dumps(value, indent=2, default=str))
                self.assertNotIn("response_completeness", json.loads(rendered))

    def test_compact_fit_is_unchanged(self):
        value = envelope(rows(300))
        compact = json.dumps(value, separators=(",", ":"))
        limit = len(compact) + 10
        self.assertGreater(len(json.dumps(value, indent=2)), limit)
        rendered, truncated = dump_json_with_truncation(value, limit)
        self.assertFalse(truncated)
        self.assertEqual(rendered, compact)

    def test_oversize_non_list_response_keeps_the_general_projection(self):
        value = envelope({"config": {"description": "z" * 70_000, "alias": "synthetic"}})
        result, truncated = self.render(value)
        self.assertTrue(truncated)
        self.assertNotIn("projection", result["response_completeness"])
        self.assertEqual(dump_json_with_truncation(value), general(value))

    def test_governed_and_failure_responses_keep_the_general_projection(self):
        children = [{"state": "observing", "reason": "x" * 2000} for _ in range(100)]
        cases = {
            "receipt branch list": envelope(
                {"task_id": "t" * 32, "plan_id": "p" * 32, "children": children},
                operation="apply_change_plan"),
            "governed identity beside a list": envelope(
                {"plan_id": "p" * 32, "plan_hash": "h" * 64, "items": children},
                operation="get_change_plan"),
        }
        failure = {"operation": "list_automations", "success": False, "error": "x",
                   "error_code": "synthetic", "data": children}
        cases["failure"] = failure
        for name, value in cases.items():
            with self.subTest(case=name):
                result, truncated = self.render(value, 20_000)
                self.assertTrue(truncated)
                self.assertNotEqual(
                    result["response_completeness"].get("projection"), "list_first_page")
                self.assertEqual(dump_json_with_truncation(value, 20_000), general(value, 20_000))
        governed, _ = self.render(cases["governed identity beside a list"], 20_000)
        self.assertEqual(governed["data"]["plan_id"], "p" * 32)

    def test_logbook_keeps_its_separately_sized_refusal(self):
        # The native logbook route sizes data before the envelope; an oversize
        # logbook body still fails closed exactly as before.
        value = envelope([{"entity_id": "sensor.s", "message": "m" * 2000}] * 20,
                         operation="get_logbook")
        for render in (dump_json_with_truncation, general):
            with self.assertRaisesRegex(ValueError, "logbook receipt"):
                render(value, 20_000)

    def test_rows_are_never_cut(self):
        source = [{"entity_id": f"sensor.s{index}", "payload": "q" * (index % 97 + 300)}
                  for index in range(2000)]
        for limit in (2_000, 9_999, 60_000):
            with self.subTest(limit=limit):
                result, _ = self.render(envelope({"states": source}), limit)
                page = result["data"]["states"]
                completeness = self.assert_first_page(
                    result, page, source, "/data/states", limit)
                # The next row would not have fit: the page is maximal.
                nxt = json.dumps(source[completeness["returned_count"]], separators=(",", ":"))
                self.assertGreater(len(json.dumps(result, separators=(",", ":"))) + len(nxt) + 1,
                                   limit - 2 * len(str(len(source))))

    def test_first_page_is_deterministic(self):
        value = envelope({"automations": rows()})
        self.assertEqual(dump_json_with_truncation(value)[0], dump_json_with_truncation(value)[0])


if __name__ == "__main__":
    unittest.main()
