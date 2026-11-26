"""HAMCP-441: ``result_fields`` enrichment and membership keys on ``ha_search`` (9.2.0).

On ha-mcp 9.2.0 (``51e3aac7``) ``result_fields`` accepts the six base record
keys, the composed ``name``, four enrichment keys and two membership keys, and
rejects any other name (``src/ha_mcp/tools/search/entities.py:59, :62,
:131-136, :158-166``). Enrichment writes ``area`` and ``floor`` names (or
``null``), label names and string aliases (:306-314, merged at :735-770);
membership writes ``is_group`` (a bool) and the direct member entity ids
(:182-223). The component path writes the same keys
(``custom_components/ha_mcp_tools/websocket_api/search.py:520-523,
:600-614``). Row 25's closed key set (HAMCP-371 S10) was built from receipts
with no ``result_fields`` call, so it withheld all six and counted them only in
``fields_withheld``; the read was not partial.

* The output-key contract admits the six keys on Row 25 by source review
  (round-7 ruling 2026-11-20); no receipt observes them. No value type is
  added: the typed keys stay the S10-2 ruling (``test_hamcp371_key_sets``).
* Enriched entity records reach the client whole and are not counted.
* Receipt output is unchanged with or without the admission.
* A key outside the set and the upstream free-text ``result_fields`` warning
  are still withheld and counted, or replaced.

Records are synthetic and injected into in-memory copies of the hash-checked
search receipts. No endpoint is contacted.

HAMCP-501 W6 (G1, as relayed by the architect): the delegated ``ha_search`` is
internal-only, reached only as one native search sub-call page. Row 25's key
set reads no argument, so the receipts replay through that route with the
fixed page arguments and the same projection. Public ``result_fields`` reach
only native search (``test_hamcp481_native_search_engine``); on the internal
route they are refused before dispatch.
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

SEARCH = "ha_search"
ENRICHMENT_SOURCE = (
    "9.2.0 source review, ha-mcp 51e3aac7 src/ha_mcp/tools/search/entities.py:59, :131-136, "
    ":306-314, :735-770; search/component.py:588-601; custom_components/ha_mcp_tools/"
    "websocket_api/search.py:520-523, :600-603 (result_fields entities[] area and floor "
    "names, label names, string aliases)"
)
MEMBERSHIP_SOURCE = (
    "9.2.0 source review, ha-mcp 51e3aac7 src/ha_mcp/tools/search/entities.py:62, :131-136, "
    ":182-223; search/response.py:570-577; custom_components/ha_mcp_tools/websocket_api/"
    "search.py:578-579, :606-614 (result_fields entities[] is_group: bool, member entity ids)"
)
# entities.py:39-49 (base), :59 (enrichment), :62 (membership), :68 (composed name).
BASE = ("entity_id", "friendly_name", "domain", "state", "score", "match_type", "name")
ENRICHMENT = ("area", "floor", "labels", "aliases")
MEMBERSHIP = ("is_group", "member_entity_ids")
NEW = ENRICHMENT + MEMBERSHIP
INJECTED = "hamcp441_injected"
RESULT_FIELDS = list(BASE[:6]) + list(NEW)
RECORDS = [
    {"entity_id": "switch.hamcp441_child", "friendly_name": "HAMCP441 child", "domain": "switch",
     "state": "off", "score": 100, "match_type": "exact", "area": "HAMCP441 kitchen",
     "floor": "HAMCP441 ground", "labels": ["HAMCP441 label"], "aliases": ["HAMCP441 alias"],
     "is_group": False},
    {"entity_id": "light.hamcp441_group", "friendly_name": "HAMCP441 group", "domain": "light",
     "state": "on", "score": 90, "match_type": "fuzzy", "area": None, "floor": None,
     "labels": [], "aliases": [], "is_group": True,
     "member_entity_ids": ["light.hamcp441_a", "light.hamcp441_b"]},
]
# The Real HA lane's child_effective_area record shape. HAMCP-501 W6: the lane
# now calls native ha_search (scripts/real_ha_contract_tests.py); this case
# keeps Row 25's area key on the internal page route.
LANE = ({"entity_id": "switch.hamcp441_child", "area": "HAMCP441 kitchen"},)
# response_helpers.py:245-268: free text naming upstream record keys.
RESULT_FIELDS_WARNING = (
    "result_fields ['hamcp441'] matched no record keys — records came out empty. "
    "Available keys: ['domain', 'entity_id']"
)

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
            if call["public_tool"] == SEARCH:
                yield set_name, call, rec["exchange"]["response"]["result"]


def run(cases):
    # HAMCP-501 W6: one sub-call page per receipt (Row 25 reads no argument).
    return [out for out, _sent in replay.replay("9.2.0", [
        (SEARCH, replay._arguments(call), replay._with(result, content))
        for call, result, content in cases])]


def run_public(cases, result_fields=RESULT_FIELDS):
    """The recorded public arguments with ``result_fields``: (reply, sent)."""

    return replay.replay("9.2.0", [
        (SEARCH, {**call["public_arguments"], "result_fields": list(result_fields)},
         replay._with(result, content))
        for call, result, content in cases])


def with_entities(content, records, warnings=None):
    copy = deepcopy(content)
    copy["entities"] = deepcopy(list(records))
    if warnings is not None:
        copy["warnings"] = list(warnings)
    return copy


def without_admission():
    row = keys.ROWS[SEARCH]
    return dataclasses.replace(row, admitted=row.admitted - set(NEW))


def project(content, row=None):
    """Row 25's key-set step alone, under ``row`` (default: the shipped row)."""

    rows = {SEARCH: row} if row is not None else {}
    with mock.patch.dict(keys.ROWS, rows):
        return keys.projection(SEARCH, lambda payload, _arguments: payload)(deepcopy(content), {})


class Contract(unittest.TestCase):

    def test_result_fields_keys_are_admitted_by_source_review(self):
        row = replay.READS[SEARCH]
        for name in NEW:
            source = ENRICHMENT_SOURCE if name in ENRICHMENT else MEMBERSHIP_SOURCE
            with self.subTest(name=name):
                self.assertEqual(row["admitted"][name], source)
                self.assertEqual(row["new_in_9_2_0"][name], {
                    "observed_9_1_2_profiles": [], "observed_9_2_0_profiles": [],
                    "source": source, "status": "admitted"})
                self.assertIn(name, keys.ROWS[SEARCH].admitted)
                self.assertNotIn(name, keys.ROWS[SEARCH].drops)
        self.assertEqual(row["value_types"], {})
        self.assertEqual(keys.ROWS[SEARCH].types, {})
        # No search receipt passed result_fields.
        for set_name, call, _result in calls():
            with self.subTest(set=set_name, call=call["id"]):
                self.assertNotIn("result_fields", call["public_arguments"])

    def test_every_result_fields_name_is_admitted(self):
        for name in BASE + NEW:
            with self.subTest(name=name):
                self.assertIn(name, keys.ROWS[SEARCH].admitted)


class Search(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        restore_after_class(cls)
        cls.cases = [(s, call, result, result["structuredContent"]) for s, call, result in calls()]
        cls.plain = run([(c, r, sc) for _s, c, r, sc in cls.cases])

    def setUp(self):
        self.addCleanup(Guard.restore, Guard().capture())
        self.assertTrue(self.cases)

    def outputs(self, records, warnings=None):
        return zip(self.cases, run([(c, r, with_entities(sc, records, warnings))
                                    for _s, c, r, sc in self.cases]), self.plain)

    def test_enriched_records_reach_the_client_uncounted(self):
        for (set_name, call, _r, _c), out, plain in self.outputs(RECORDS):
            with self.subTest(set=set_name, call=call["id"]):
                self.assertIs(out["success"], True, out.get("details"))
                self.assertEqual(out["data"]["entities"], RECORDS)
                for field in replay.COUNT_FIELDS:
                    self.assertEqual(out["data"][field], plain["data"][field])
                self.assertNotIn(keys.COLLECTION_WITHHELD, json.dumps(out))

    def test_lane_area_projection_keeps_area(self):
        for (set_name, call, _r, _c), out, plain in self.outputs(LANE):
            with self.subTest(set=set_name, call=call["id"]):
                self.assertIs(out["success"], True, out.get("details"))
                self.assertEqual(out["data"]["entities"], list(LANE))
                self.assertEqual(out["data"]["fields_withheld"], plain["data"]["fields_withheld"])

    def test_without_the_admission_the_keys_are_withheld_and_counted(self):
        # The HAMCP-441 defect, kept as a regression: the S10 row loses them silently.
        for set_name, call, _r, content in self.cases:
            with self.subTest(set=set_name, call=call["id"]):
                enriched = with_entities(content, RECORDS)
                fixed, defect = project(enriched), project(enriched, without_admission())
                self.assertEqual(fixed["entities"], RECORDS)
                self.assertEqual(defect["entities"],
                                 [{k: v for k, v in r.items() if k not in NEW} for r in RECORDS])
                self.assertEqual(defect["fields_withheld"], fixed["fields_withheld"] + len(NEW))
                self.assertNotIn(keys.NOTES, defect)
                lane = with_entities(content, LANE)
                self.assertEqual(project(lane, without_admission())["entities"],
                                 [{"entity_id": "switch.hamcp441_child"}])

    def test_public_result_fields_never_reach_the_internal_route(self):
        # HAMCP-501 W6 (G1): a public result_fields call is not a sub-call
        # page; it is refused before dispatch, enriched content or not.
        cases = [(c, r, with_entities(sc, RECORDS)) for _s, c, r, sc in self.cases]
        for result_fields in (RESULT_FIELDS, ("entity_id", "area")):
            for ((set_name, call, _r, _c), (out, sent)) in zip(
                    self.cases, run_public(cases, result_fields)):
                with self.subTest(set=set_name, call=call["id"], result_fields=result_fields):
                    self.assertEqual(sent, [])
                    self.assertIs(out["success"], False)
                    self.assertEqual(out["details"]["failure_category"], "prohibited_delegation")
                    self.assertNotIn("HAMCP441", json.dumps(out))

    def test_receipt_shape_is_unchanged(self):
        for set_name, call, result in calls():
            content = result["structuredContent"]
            with self.subTest(set=set_name, call=call["id"]):
                self.assertFalse(set(NEW) & keys._names(content, keys.ROWS[SEARCH], set()))
                self.assertEqual(project(content), project(content, without_admission()))

    def test_other_keys_and_the_free_text_warning_stay_out(self):
        injected = [{**r, INJECTED: "x"} for r in RECORDS]
        for (set_name, call, _r, _c), out, plain in self.outputs(injected, [RESULT_FIELDS_WARNING]):
            with self.subTest(set=set_name, call=call["id"]):
                self.assertEqual(out["data"]["entities"], RECORDS)
                self.assertNotIn(INJECTED, json.dumps(out))
                self.assertNotIn("Available keys", json.dumps(out))
                self.assertIn(keys.REPLACED_WARNING, out["data"]["warnings"])
                self.assertEqual(out["data"]["fields_withheld"], plain["data"]["fields_withheld"] + 1)
                self.assertEqual(out["data"]["upstream_warnings_replaced"],
                                 plain["data"]["upstream_warnings_replaced"] + 1)


if __name__ == "__main__":
    unittest.main()
