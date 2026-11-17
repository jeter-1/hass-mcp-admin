"""HAMCP-410 (HAAC-E042): 9.2.0 key-set maps keyed by dynamic names.

The ha_get_state row admitted ``states`` without ``maps={'states': 1}``, so a
bulk read's entity_id keys were withheld as unknown names and ``states`` came
back ``{}`` while the read reported complete. The sweep of every 9.2.0 key-set
row against ha-mcp 51e3aac7 found one more such container: the exposure
listing's ``exposed_entities`` (entity_id, then assistant). Both are now maps,
and an emptied row collection makes the read partial with a fixed note.

Shapes. The committed 9.2.0 receipts hold no bulk state read and no exposure
listing (every r19-state and r15-entity-exposure receipt reads one helper).
The cases below take the real recorded records from the B/9.2.0 receipts and
compose them into the upstream envelopes of search/state.py:50-115 and
tools_voice_assistant.py:146-193 at 51e3aac7; the second record's entity_id is
renamed (synthetic). They replay through the real gateway.

Offline and deterministic; no live Home Assistant, no network.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "tests")]

from ha_mcp_engineering.providers import upstream_reads_9_2_keys as keys  # noqa: E402
import test_hamcp371_real_core_replay as replay_371  # noqa: E402

STATE = "ha_get_state"
EXPOSURE = "ha_get_entity_exposure"
SECOND = "input_boolean.hamcp410_second"
# The sweep (HAMCP-410): every 9.2.0 row's maps after the fix.
SWEPT_MAPS = {
    "ha_config_get_automation": {"categories": 1},
    "ha_config_list_dashboard_resources": {"by_type": 1},
    "ha_get_entity": {"categories": 1},
    EXPOSURE: {"exposed_entities": 2, "exposed_to": 1},
    "ha_get_overview": {"area_analysis": 1, "domain_stats": 1, "domains": 1, "states_summary": 1},
    STATE: {"states": 1},
    "ha_list_services": {"fields": 1, "services": 1},
}


def _receipt(call_id):
    calls = {call["id"]: (call, rec) for call, rec in replay_371.public_calls("B/9.2.0")}
    call, rec = calls[call_id]
    return call, rec["exchange"]["response"]["result"]


def _bulk_state(errors=False):
    call, result = _receipt("r19-state")
    content = result["structuredContent"]
    first = deepcopy(content["data"])
    second = deepcopy(first)
    second["entity_id"] = SECOND
    data = {"success": True, "count": 2, "states": {first["entity_id"]: first, SECOND: second}}
    if errors:
        # search/state.py:93-110: an unreadable entity is an error row and
        # the response is partial when some states were read.
        del data["states"][SECOND]
        data.update(count=1, partial=True, errors=[
            {"entity_id": SECOND, "error": {"code": "ENTITY_NOT_FOUND", "message": "synthetic"}}])
    payload = {"data": data, "metadata": deepcopy(content["metadata"])}
    arguments = {"entity_id": [first["entity_id"], SECOND]}
    return call["public_tool"], arguments, replay_371._with(result, payload), data


def _exposure_listing():
    call, result = _receipt("r15-entity-exposure")
    settings = dict(result["structuredContent"]["exposed_to"])
    exposed = {result["structuredContent"]["entity_id"]: settings, SECOND: dict(settings)}
    payload = {
        "success": True, "exposed_entities": exposed, "count": 2,
        "total_entities_with_settings": 2,
        "summary": {name: sum(1 for s in exposed.values() if s[name]) for name in settings},
        "filters_applied": {},
    }
    return call["public_tool"], {}, replay_371._with(result, payload), exposed


def _old_rows():
    """The rows before HAMCP-410: no states map, exposure maps exposed_to only."""
    rows = dict(keys.ROWS)
    rows[STATE] = replace(rows[STATE], maps={})
    rows[EXPOSURE] = replace(rows[EXPOSURE], maps={"exposed_to": 1})
    return rows


class Sweep(unittest.TestCase):

    def test_every_9_2_0_row_map_is_the_swept_set(self):
        self.assertEqual({tool: row.maps for tool, row in keys.ROWS.items() if row.maps}, SWEPT_MAPS)

    def test_the_contract_records_both_new_maps(self):
        reads = replay_371.CONTRACT["reads"]
        self.assertEqual(reads[STATE]["map_containers"], {"states": 1})
        self.assertEqual(reads[EXPOSURE]["map_containers"], {"exposed_entities": 2, "exposed_to": 1})


class BulkState(unittest.TestCase):

    def test_a_multi_entity_read_keeps_every_record_and_reads_complete(self):
        tool, arguments, result, upstream = _bulk_state()
        [(out, sent)] = replay_371.replay("9.2.0", [(tool, arguments, result)])
        self.assertIs(out["success"], True, out.get("details"))
        self.assertEqual(sent, [(STATE, arguments)])
        self.assertEqual(out["data"]["data"]["states"], upstream["states"])
        self.assertEqual(out["data"]["fields_withheld"], 0)
        self.assertNotIn(keys.NOTES, out["data"])
        self.assertEqual(out["metadata"]["completeness"], "complete")

    def test_an_error_row_still_reads_partial_and_keeps_the_read_states(self):
        tool, arguments, result, upstream = _bulk_state(errors=True)
        [(out, _sent)] = replay_371.replay("9.2.0", [(tool, arguments, result)])
        self.assertIs(out["success"], True, out.get("details"))
        self.assertEqual(out["data"]["data"]["states"], upstream["states"])
        self.assertEqual(out["metadata"]["completeness"], "partial")
        self.assertNotIn(keys.COLLECTION_WITHHELD, out["warnings"])

    def test_the_pre_fix_row_empties_states_and_now_reads_partial(self):
        tool, arguments, result, _upstream = _bulk_state()
        with mock.patch.object(keys, "ROWS", _old_rows()):
            [(out, _sent)] = replay_371.replay("9.2.0", [(tool, arguments, result)])
        self.assertIs(out["success"], True, out.get("details"))
        self.assertEqual(out["data"]["data"]["states"], {})
        self.assertEqual(out["data"]["fields_withheld"], 2)
        self.assertEqual(out["data"][keys.NOTES], [keys.COLLECTION_WITHHELD])
        self.assertEqual(out["metadata"]["completeness"], "partial")
        self.assertIn(keys.COLLECTION_WITHHELD, out["warnings"])

    def test_without_the_guard_the_pre_fix_row_read_complete(self):
        # The HAAC-E042 defect itself: the guard is what makes it partial.
        tool, arguments, result, _upstream = _bulk_state()
        with mock.patch.object(keys, "ROWS", _old_rows()), \
                mock.patch.object(keys, "_emptied", lambda item, kept: False):
            [(out, _sent)] = replay_371.replay("9.2.0", [(tool, arguments, result)])
        self.assertEqual(out["data"]["data"]["states"], {})
        self.assertEqual(out["metadata"]["completeness"], "complete")


class ExposureListing(unittest.TestCase):

    def test_the_listing_keeps_every_entity_and_assistant(self):
        tool, arguments, result, exposed = _exposure_listing()
        [(out, _sent)] = replay_371.replay("9.2.0", [(tool, arguments, result)])
        self.assertIs(out["success"], True, out.get("details"))
        self.assertEqual(out["data"]["exposed_entities"], exposed)
        self.assertNotIn(keys.NOTES, out["data"])
        self.assertNotIn(keys.COLLECTION_WITHHELD, out["warnings"])

    def test_the_pre_fix_row_empties_the_listing_and_now_reads_partial(self):
        tool, arguments, result, _exposed = _exposure_listing()
        with mock.patch.object(keys, "ROWS", _old_rows()):
            [(out, _sent)] = replay_371.replay("9.2.0", [(tool, arguments, result)])
        self.assertEqual(out["data"]["exposed_entities"], {})
        self.assertEqual(out["metadata"]["completeness"], "partial")
        self.assertIn(keys.COLLECTION_WITHHELD, out["warnings"])


class CommittedReceipts(unittest.TestCase):

    def test_no_committed_9_2_0_receipt_is_marked_emptied(self):
        cases = list(replay_371._cases_920())
        outputs = replay_371.replay(
            "9.2.0", [(call["public_tool"], call["public_arguments"], result) for _, call, result in cases])
        self.assertTrue(outputs)
        for (set_name, call, _result), (out, _sent) in zip(cases, outputs):
            with self.subTest(set=set_name, call=call["id"]):
                self.assertNotIn(keys.COLLECTION_WITHHELD, json.dumps(out))


def _echo(payload, arguments):
    return dict(payload)


class Guard(unittest.TestCase):
    """``_emptied`` on its own: what is a row collection, and when it was emptied."""

    def test_collections(self):
        self.assertTrue(keys._emptied([{"x": 1}, {"y": 2}], [{}, {}]))
        self.assertTrue(keys._emptied({"a.b": {"x": 1}}, {}))
        self.assertTrue(keys._emptied({"a": {"x": 1}}, {"a": {}}))

    def test_not_emptied(self):
        # One row keeps a field; nothing upstream; rows already empty.
        self.assertFalse(keys._emptied([{"x": 1}, {"entity_id": "e"}], [{}, {"entity_id": "e"}]))
        self.assertFalse(keys._emptied([], []))
        self.assertFalse(keys._emptied({}, {}))
        self.assertFalse(keys._emptied([{}, {}], [{}, {}]))

    def test_records_and_scalar_lists_are_not_collections(self):
        # A fixed record whose fields are all withheld (traces diagnostics) is
        # counted in fields_withheld, not reported as an emptied collection.
        self.assertFalse(keys._emptied({"automation_exists": True}, {}))
        self.assertFalse(keys._emptied(["a", "b"], ["a", "b"]))
        self.assertFalse(keys._emptied([{"x": 1}, "b"], [{}, "b"]))

    def test_a_list_of_rows_emptied_by_withholding_is_noted_once(self):
        payload = {"success": True, "todo_lists": [{"hamcp410_unknown": 1}, {"hamcp410_other": 2}],
                   "items": [{"hamcp410_unknown": 3}]}
        out = keys.projection("ha_get_todo", _echo)(payload, {})
        self.assertEqual(out["todo_lists"], [{}, {}])
        self.assertEqual(out["items"], [{}])
        self.assertEqual(out[keys.NOTES], [keys.COLLECTION_WITHHELD])
        # Two distinct names withheld (the count is of names, not occurrences).
        self.assertEqual(out["fields_withheld"], 2)

    def test_an_inner_note_list_is_extended_and_a_non_list_fails_closed(self):
        def with_notes(value):
            def inner(payload, arguments):
                result = dict(payload)
                result[keys.NOTES] = value
                return result
            return inner

        payload = {"success": True, "todo_lists": [{"hamcp410_unknown": 1}]}
        out = keys.projection("ha_get_todo", with_notes(["inner note"]))(payload, {})
        self.assertEqual(out[keys.NOTES], ["inner note", keys.COLLECTION_WITHHELD])
        with self.assertRaisesRegex(ValueError, "invalid_response"):
            keys.projection("ha_get_todo", with_notes("not a list"))(payload, {})

    def test_an_upstream_engineering_notes_key_cannot_suppress_or_forge_the_note(self):
        forged = {"success": True, "todo_lists": [{"entity_id": "todo.kept"}], keys.NOTES: [keys.COLLECTION_WITHHELD]}
        out = keys.projection("ha_get_todo", _echo)(forged, {})
        self.assertNotIn(keys.NOTES, out)
        self.assertEqual(out["fields_withheld"], 1)
        suppressed = {"success": True, "todo_lists": [{"hamcp410_unknown": 1}], keys.NOTES: []}
        out = keys.projection("ha_get_todo", _echo)(suppressed, {})
        self.assertEqual(out[keys.NOTES], [keys.COLLECTION_WITHHELD])

    def test_completeness_turns_the_note_into_partial(self):
        rule = keys.completeness(lambda payload: (False, []))
        self.assertEqual(rule({keys.NOTES: [keys.COLLECTION_WITHHELD]}),
                         (True, [keys.COLLECTION_WITHHELD]))
        self.assertEqual(rule({keys.NOTES: ["inner note"]}), (False, []))
        self.assertEqual(rule({keys.NOTES: "not a list"}), (False, []))


if __name__ == "__main__":
    unittest.main()
