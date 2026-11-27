"""HAMCP-432: the first-page bound keeps a list reply's own counts true.

HAMCP-418 returned whole leading rows of an oversize ``data`` list, but a
bare reply (no ``data`` key) kept the general projection and lost the whole
list, and a page kept its parent's ``count`` and ``truncated`` values from
the full list. All values are synthetic.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hass_mcp_engineering_beta"))
from ha_mcp_engineering.models.responses import MAX_CHARS, SuccessResponse, dump_json_with_truncation
from tests.test_hamcp418_list_first_page import general, rows


def render(case, value, limit=MAX_CHARS):
    original = deepcopy(value)
    rendered, truncated = dump_json_with_truncation(value, limit)
    case.assertEqual(value, original, "projection must not mutate its input")
    case.assertLessEqual(len(rendered.encode()), limit)
    return json.loads(rendered), truncated


class BareReplyTests(unittest.TestCase):
    def test_bare_reply_returns_a_first_page_of_its_largest_list(self):
        source = rows()
        value = {"count": len(source), "devices": source, "truncated": False, "filter": "all"}
        result, truncated = render(self, value)
        self.assertTrue(truncated)
        completeness = result["response_completeness"]
        self.assertEqual(completeness["projection"], "list_first_page")
        self.assertEqual(completeness["list_path"], "/devices")
        kept = completeness["returned_count"]
        self.assertGreater(kept, 0)
        self.assertEqual(result["devices"], source[:kept])
        self.assertEqual(kept + completeness["omitted_count"], len(source))
        # The reply's own counts describe this reply.
        self.assertEqual(result["count"], kept)
        self.assertIs(result["truncated"], True)
        self.assertEqual(result["filter"], "all")
        self.assertNotIn("count_note", completeness)
        notice = completeness["notice"]
        self.assertTrue(notice.startswith(f"... [truncated at {MAX_CHARS} chars]"))
        self.assertIn(f"first {kept} of {len(source)} rows in the order returned", notice)
        # The general projection used to return none of these rows.
        self.assertNotIn("devices", json.loads(general(value)[0]))

    def test_bare_reply_largest_list_is_chosen_and_others_kept(self):
        source = rows()
        value = {"labels": ["a", "b"], "entities": source}
        result, _ = render(self, value)
        self.assertEqual(result["response_completeness"]["list_path"], "/entities")
        self.assertEqual(result["labels"], ["a", "b"])

    def test_root_warnings_and_receipt_branches_are_never_paged(self):
        bulky = ["w" * 1000] * 80
        for key in ("warnings", "metadata", "children"):
            with self.subTest(key=key):
                value = {key: bulky, "note": "synthetic"}
                result, truncated = render(self, value)
                self.assertTrue(truncated)
                self.assertNotEqual(result["response_completeness"].get("projection"), "list_first_page")
                self.assertEqual(dump_json_with_truncation(value), general(value))

    def test_bare_failure_logbook_and_governed_identity_keep_the_general_projection(self):
        children = [{"state": "observing", "reason": "x" * 2000} for _ in range(100)]
        cases = {
            "failure": {"success": False, "error_code": "synthetic", "items": children},
            "governed identity beside a list": {"plan_id": "p" * 32, "items": children},
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                result, truncated = render(self, value, 20_000)
                self.assertTrue(truncated)
                self.assertNotEqual(result["response_completeness"].get("projection"), "list_first_page")
                self.assertEqual(dump_json_with_truncation(value, 20_000), general(value, 20_000))

    def test_a_value_with_a_non_list_data_key_is_not_paged_at_the_root(self):
        value = {"data": "synthetic", "rows": rows()}
        result, truncated = render(self, value)
        self.assertTrue(truncated)
        self.assertNotEqual(result["response_completeness"].get("projection"), "list_first_page")

    def test_small_bare_reply_is_unchanged(self):
        value = {"count": 5, "devices": rows(5), "truncated": False}
        rendered, truncated = dump_json_with_truncation(value)
        self.assertFalse(truncated)
        self.assertEqual(json.loads(rendered), value)


class CountTruthTests(unittest.TestCase):
    def envelope(self, data):
        return SuccessResponse(operation="synthetic_list", summary="synthetic", data=data,
                               request_id="req-synthetic").as_dict()

    def test_matching_count_and_returned_become_the_rows_kept(self):
        source = rows()
        value = self.envelope({"automations": source, "count": 2000, "returned": 2000,
                               "total": 9000, "total_count": 9000, "truncated": False})
        result, _ = render(self, value)
        data, completeness = result["data"], result["response_completeness"]
        kept = completeness["returned_count"]
        self.assertEqual((data["count"], data["returned"]), (kept, kept))
        self.assertIs(data["truncated"], True)
        # Totals describe the whole list and stay.
        self.assertEqual((data["total"], data["total_count"]), (9000, 9000))
        self.assertEqual(completeness["total_count"], 9000)
        self.assertNotIn("count_note", completeness)

    def test_a_count_that_is_the_tools_own_value_stays_and_is_explained(self):
        source = rows()
        for count in (5000, 10):
            with self.subTest(count=count):
                value = self.envelope({"automations": source, "count": count})
                result, _ = render(self, value)
                completeness = result["response_completeness"]
                self.assertEqual(result["data"]["count"], count)
                self.assertEqual(
                    completeness["count_note"],
                    "count is the tool's own value; returned_count is the rows in this reply.")
                self.assertLess(completeness["returned_count"], len(source))

    def test_only_int_counts_are_rewritten(self):
        source = rows()
        for count in (True, "2000", 2000.0, None):
            with self.subTest(count=count):
                value = self.envelope({"automations": source, "count": count})
                result, _ = render(self, value)
                self.assertEqual(result["data"]["count"], count)
                self.assertNotIn("count_note", result["response_completeness"])

    def test_an_absent_truncated_key_is_not_added(self):
        result, _ = render(self, self.envelope({"automations": rows()}))
        self.assertNotIn("truncated", result["data"])
        self.assertNotIn("count", result["data"])

    def test_a_failed_page_restores_the_counts_for_the_general_projection(self):
        # One row larger than the bound: no first page, and today's projection
        # sees the reply exactly as it was.
        source = [{"entity_id": "automation.huge", "note": "y" * 70_000},
                  {"entity_id": "automation.small"}]
        value = self.envelope({"automations": source, "count": 2, "truncated": False})
        self.assertEqual(dump_json_with_truncation(value), general(value))

    def test_page_with_rewritten_counts_is_maximal_and_within_the_bound(self):
        source = [{"entity_id": f"sensor.s{index}", "payload": "q" * (index % 97 + 300)}
                  for index in range(2000)]
        for limit in (2_000, 9_999, 60_000):
            with self.subTest(limit=limit):
                value = self.envelope({"states": source, "count": 2000, "returned": 2000,
                                       "truncated": False})
                result, _ = render(self, value, limit)
                kept = result["response_completeness"]["returned_count"]
                self.assertEqual(result["data"]["states"], source[:kept])
                self.assertEqual(result["data"]["count"], kept)
                nxt = json.dumps(source[kept], separators=(",", ":"))
                self.assertGreater(len(json.dumps(result, separators=(",", ":"))) + len(nxt) + 1,
                                   limit - 2 * len(str(len(source))))


if __name__ == "__main__":
    unittest.main()
