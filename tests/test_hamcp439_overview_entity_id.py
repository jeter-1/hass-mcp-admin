"""HAMCP-439: ``entity_id`` on full-detail ``ha_get_overview`` entity rows (9.2.0).

On ha-mcp 9.2.0 (``51e3aac7``, ``src/ha_mcp/tools/smart_search/_overview.py``)
``detail_level="full"`` turns ``include_entity_id`` on (:285-286), each entity
row then carries ``entity_id`` (:364-365) and the rows sit under
``domain_stats.<domain>.entities`` (:445). A row's keys are ``friendly_name``
(:358), ``name`` when it differs (:361-363), ``entity_id`` and ``state``
(:366-367). Row 17's closed key set (HAMCP-371 S10) was built from receipts
with no full-detail call, so it withheld ``entity_id`` and counted it only in
``fields_withheld``; the read was not partial.

* The output-key contract admits ``entity_id`` on Row 17 by source review
  (round-6 ruling 2026-11-19); no receipt observes it.
* Full-detail rows reach the client with ``entity_id`` and are not counted.
* Standard-detail output is unchanged: every overview receipt projects the same
  with or without the admission, and a standard row passes as before.
* A key outside the set is still withheld and counted, on an entity row and at
  the top level (``device_types`` and ``service_availability``, full only).

Rows are synthetic and injected into in-memory copies of the hash-checked
overview receipts. No endpoint is contacted.
"""
from __future__ import annotations

from copy import deepcopy
import dataclasses
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from ha_mcp_engineering.providers import upstream_reads_9_2_keys as keys  # noqa: E402
import test_hamcp371_real_core_replay as replay  # noqa: E402
from tests.support.process_isolation import Guard, restore_after_class  # noqa: E402

OVERVIEW = "ha_get_overview"
SOURCE = (
    "9.2.0 source review, ha-mcp 51e3aac7 src/ha_mcp/tools/smart_search/_overview.py:285-286, "
    ":364-365, :445 (detail_level=full domain_stats.<d>.entities[].entity_id)"
)
# Every key _overview.py:358-367 writes into an entity row.
ROW_KEYS = ("friendly_name", "name", "entity_id", "state")
INJECTED = "hamcp439_injected"
FULL_ROWS = [
    {"friendly_name": "HAMCP439 kitchen", "entity_id": "automation.hamcp439_kitchen", "state": "on"},
    {"friendly_name": "HAMCP439 porch", "name": "HAMCP439 owner porch",
     "entity_id": "automation.hamcp439_porch", "state": "off"},
]
STANDARD_ROWS = [
    {"friendly_name": "HAMCP439 kitchen"},
    {"friendly_name": "HAMCP439 porch", "name": "HAMCP439 owner porch"},
]
FULL_ONLY = {"device_types": {"hamcp439_class": 1},
             "service_availability": {"hamcp439": {"count": 1, "services": ["hamcp439"]}}}

_BEFORE = None


def setUpModule():
    global _BEFORE
    _BEFORE = Guard().capture()


def tearDownModule():
    changes = Guard.changes(_BEFORE)
    if changes:
        raise AssertionError(f"{__name__} left process-wide state: " + "; ".join(changes))


def calls(sets=("A/9.2.0", "B/9.2.0")):
    for set_name in sets:
        for call, rec in replay.public_calls(set_name):
            if call["public_tool"] == OVERVIEW:
                yield set_name, call, rec["exchange"]["response"]["result"]


def run(cases):
    return [out for out, _sent in replay.replay("9.2.0", [
        (OVERVIEW, call["public_arguments"], replay._with(result, content))
        for call, result, content in cases])]


def with_rows(content, rows, extra=None):
    copy = deepcopy(content)
    copy["domain_stats"]["automation"]["entities"] = deepcopy(rows)
    copy.update(deepcopy(extra or {}))
    return copy


def without_entity_id():
    row = keys.ROWS[OVERVIEW]
    return dataclasses.replace(row, admitted=row.admitted - {"entity_id"})


def project(content, row=None):
    """Row 17's key-set step alone, under ``row`` (default: the shipped row)."""

    rows = {OVERVIEW: row} if row is not None else {}
    with mock.patch.dict(keys.ROWS, rows):
        return keys.projection(OVERVIEW, lambda payload, _arguments: payload)(deepcopy(content), {})


class Contract(unittest.TestCase):

    def test_entity_id_is_admitted_by_source_review(self):
        row = replay.READS[OVERVIEW]
        self.assertEqual(row["admitted"]["entity_id"], SOURCE)
        self.assertEqual(row["new_in_9_2_0"]["entity_id"], {
            "observed_9_1_2_profiles": [], "observed_9_2_0_profiles": [],
            "source": SOURCE, "status": "admitted"})
        self.assertIn("entity_id", keys.ROWS[OVERVIEW].admitted)
        # No overview receipt made a full-detail call.
        for set_name, call, _result in calls():
            with self.subTest(set=set_name, call=call["id"]):
                self.assertNotEqual(call["public_arguments"].get("detail_level"), "full")

    def test_every_entity_row_key_is_admitted(self):
        for name in ROW_KEYS:
            with self.subTest(name=name):
                self.assertIn(name, keys.ROWS[OVERVIEW].admitted)
                self.assertNotIn(name, keys.ROWS[OVERVIEW].drops)


class Overview(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        restore_after_class(cls)
        cls.cases = [(s, call, result, result["structuredContent"]) for s, call, result in calls()
                     if "automation" in result["structuredContent"].get("domain_stats", {})]
        cls.plain = run([(c, r, sc) for _s, c, r, sc in cls.cases])

    def setUp(self):
        self.addCleanup(Guard.restore, Guard().capture())
        self.assertTrue(self.cases)

    def rows(self, out):
        return out["data"]["domain_stats"]["automation"]["entities"]

    def test_full_detail_rows_keep_entity_id_uncounted(self):
        outputs = run([(c, r, with_rows(sc, FULL_ROWS)) for _s, c, r, sc in self.cases])
        for (set_name, call, _r, _c), out, plain in zip(self.cases, outputs, self.plain):
            with self.subTest(set=set_name, call=call["id"]):
                self.assertIs(out["success"], True, out.get("details"))
                self.assertEqual(self.rows(out), FULL_ROWS)
                for field in replay.COUNT_FIELDS:
                    self.assertEqual(out["data"][field], plain["data"][field])
                self.assertNotIn(keys.COLLECTION_WITHHELD, json.dumps(out))

    def test_without_the_admission_entity_id_is_withheld_and_counted(self):
        # The HAMCP-439 defect, kept as a regression: the S10 row loses it silently.
        for set_name, call, _r, content in self.cases:
            with self.subTest(set=set_name, call=call["id"]):
                full = with_rows(content, FULL_ROWS)
                fixed, defect = project(full), project(full, without_entity_id())
                self.assertEqual(defect["domain_stats"]["automation"]["entities"],
                                 [{k: v for k, v in r.items() if k != "entity_id"} for r in FULL_ROWS])
                self.assertEqual(defect["fields_withheld"], fixed["fields_withheld"] + 1)
                self.assertNotIn(keys.NOTES, defect)

    def test_standard_detail_shape_is_unchanged(self):
        for set_name, call, result in calls():
            content = result["structuredContent"]
            with self.subTest(set=set_name, call=call["id"]):
                self.assertNotIn("entity_id", keys._names(content, keys.ROWS[OVERVIEW], set()))
                self.assertEqual(project(content), project(content, without_entity_id()))
        outputs = run([(c, r, with_rows(sc, STANDARD_ROWS)) for _s, c, r, sc in self.cases])
        for (set_name, call, _r, _c), out, plain in zip(self.cases, outputs, self.plain):
            with self.subTest(set=set_name, call=call["id"], rows="standard"):
                self.assertIs(out["success"], True, out.get("details"))
                self.assertEqual(self.rows(out), STANDARD_ROWS)
                self.assertNotIn("entity_id", json.dumps(out["data"]))
                for field in replay.COUNT_FIELDS:
                    self.assertEqual(out["data"][field], plain["data"][field])

    def test_other_keys_are_still_withheld_and_counted(self):
        injected = [{**row, INJECTED: "x"} for row in FULL_ROWS]
        outputs = run([(c, r, with_rows(sc, injected)) for _s, c, r, sc in self.cases])
        for (set_name, call, _r, _c), out, plain in zip(self.cases, outputs, self.plain):
            with self.subTest(set=set_name, call=call["id"], where="entity row"):
                self.assertEqual(self.rows(out), FULL_ROWS)
                self.assertNotIn(INJECTED, json.dumps(out))
                self.assertEqual(out["data"]["fields_withheld"], plain["data"]["fields_withheld"] + 1)
        outputs = run([(c, r, with_rows(sc, FULL_ROWS, FULL_ONLY)) for _s, c, r, sc in self.cases])
        for (set_name, call, _r, _c), out, plain in zip(self.cases, outputs, self.plain):
            with self.subTest(set=set_name, call=call["id"], where="top level"):
                self.assertEqual(self.rows(out), FULL_ROWS)
                for name in FULL_ONLY:
                    self.assertNotIn(name, out["data"])
                self.assertNotIn("hamcp439", json.dumps(out["data"]).replace("HAMCP439", "")
                                 .replace("automation.hamcp439_", ""))
                self.assertGreater(out["data"]["fields_withheld"], plain["data"]["fields_withheld"])


if __name__ == "__main__":
    unittest.main()
