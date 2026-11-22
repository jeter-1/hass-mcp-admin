"""HAMCP-371 S11: ``name`` on search and overview rows, and ``ha_mcp_update``.

Research G rulings 2c and 2d. ``name`` was admitted on both reads in S10's
closed key sets (``upstream_reads_9_2_keys.ROWS``); S11 closes the overview's
``ha_mcp_update`` object to three keys. Paths are ha-mcp ``51e3aac7``
``src/ha_mcp/``.

* ``ha_search``: on profile B (component 9.2.0) entity rows carry ``name``,
  the composed name the component reports. It reaches the client unchanged and
  is not counted. On profile A (no component) it is absent from entity rows.
  Every upstream warning in the search receipts is on the reviewed list.
* ``ha_get_overview``: ``name`` on an entity row is admitted and not counted.
  Upstream writes it only when the composed name differs from
  ``friendly_name`` (``tools/smart_search/_overview.py:361-363``); the lane
  seeded no such entity and every receipt's entity lists are empty, so the row
  here is synthetic and this occurrence is unobserved until SR. (``name`` is
  observed on other overview rows.)
* ``ha_mcp_update`` on the overview (``tools/tools_search.py:741-745``) keeps
  ``current``, ``latest`` and ``update_available`` (``update_check.py:386-390``).
  ``python_required`` and ``upgrade_command`` (``:391-393``) are policy drops:
  absent from the client result and counted in ``fields_dropped_by_policy``.
  Any other key inside it is withheld and counted. The lane cannot produce the
  object (no Supervisor and no PyPI, ``update_check.py:187-214``), so it is
  injected into in-memory copies of the overview receipts.
* ``ha_get_system_health`` (``tools/tools_system.py:747-751``): Engineering's
  closed health rebuild has no ``ha_mcp_update``; the same injection leaves the
  client result unchanged and raises ``fields_withheld``.
* HAMCP-437: only 9.2.0 is admitted (D1); the 9.1.2 pass-through case is
  retired.

Receipt payloads are hash-checked through the replay module; injected values
are synthetic. No endpoint is contacted.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT), str(ROOT / "tests")]

from ha_mcp_engineering.providers import upstream_reads_9_2_keys as keys  # noqa: E402
import test_hamcp371_platform_9_2 as platform_tests  # noqa: E402
import test_hamcp371_real_core_replay as replay  # noqa: E402
from tests.support.process_isolation import Guard, restore_after_class  # noqa: E402

READS = replay.READS
OVERVIEW, SEARCH, HEALTH = "ha_get_overview", "ha_search", "ha_get_system_health"
INJECTED = "hamcp371_s11_injected"
UPDATE = {"current": "9.2.0", "latest": "9.2.1", "update_available": True}
UPDATE_DROPS = {"python_required": "3.14.0", "upgrade_command": "uvx --python 3.14.0 ha-mcp@latest"}
COMPOSED = "HAMCP371 owner HAMCP371 automation"


def calls(tool, sets=("A/9.2.0", "B/9.2.0")):
    for set_name in sets:
        for call, rec in replay.public_calls(set_name):
            if call["public_tool"] == tool:
                yield set_name, call, rec["exchange"]["response"]["result"]


# HAMCP-371 (ruling 2026-11-16 08:18, T1 item 3): every gateway this module builds
# publishes the capability catalog; each class and test restores what it changed,
# and the module proves the process-wide state after it equals the state before.
_BEFORE = None


def setUpModule():
    global _BEFORE
    _BEFORE = Guard().capture()


def tearDownModule():
    changes = Guard.changes(_BEFORE)
    if changes:
        raise AssertionError(f"{__name__} left process-wide state: " + "; ".join(changes))


def run(version, cases):
    """[(tool, call, result, structuredContent)] -> gateway client outputs."""

    return [out for out, _sent in replay.replay(version, [
        (tool, call["public_arguments"], replay._with(result, content))
        for tool, call, result, content in cases])]


def entity_rows(content):
    return [row for row in content.get("entities") or [] if isinstance(row, dict)]


def without_name(content):
    copy = deepcopy(content)
    for row in entity_rows(copy):
        row.pop("name", None)
    return copy


def with_update(content, extra=None):
    copy = deepcopy(content)
    copy["ha_mcp_update"] = {**UPDATE, **UPDATE_DROPS, **(extra or {})}
    return copy


def with_entity_name(content, name=COMPOSED):
    copy = deepcopy(content)
    stats = copy["domain_stats"]["automation"]
    stats["entities"] = [{"friendly_name": "HAMCP371 automation", "entity_id": "automation.hamcp371",
                          "state": "on", **({"name": name} if name else {})}]
    return copy


class Contract(unittest.TestCase):

    def test_overview_row(self):
        row = READS[OVERVIEW]
        for name in ("name", "ha_mcp_update", *UPDATE):
            with self.subTest(name=name):
                self.assertEqual(row["new_in_9_2_0"][name]["status"], "admitted")
                self.assertTrue(row["new_in_9_2_0"][name]["source"].startswith("research G ruling 2d"))
                self.assertIn(name, keys.ROWS[OVERVIEW].admitted)
        for name in UPDATE_DROPS:
            with self.subTest(name=name):
                self.assertIn(name, row["policy_drops"])
                self.assertIn(name, keys.ROWS[OVERVIEW].drops)
                self.assertNotIn(name, keys.ROWS[OVERVIEW].admitted)
        # No receipt carries the object or an overview entity-row name.
        for name in ("ha_mcp_update", *UPDATE):
            self.assertEqual(row["new_in_9_2_0"][name]["observed_9_2_0_profiles"], [])

    def test_search_row(self):
        self.assertIn("name", keys.ROWS[SEARCH].admitted)
        # Profile A carries ``name`` on other rows; the entity-row case is the test below.
        self.assertIn("B", READS[SEARCH]["new_in_9_2_0"]["name"]["observed_9_2_0_profiles"])


class Search(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        restore_after_class(cls)
        cls.cases = [(s, call, result, result["structuredContent"]) for s, call, result in calls(SEARCH)]
        cls.out = run("9.2.0", [(SEARCH, c, r, sc) for _s, c, r, sc in cls.cases])
        cls.bare = run("9.2.0", [(SEARCH, c, r, without_name(sc)) for _s, c, r, sc in cls.cases])

    def test_name_on_profile_b_passes_unchanged_and_uncounted(self):
        seen = 0
        for (set_name, call, _r, content), out, bare in zip(self.cases, self.out, self.bare):
            with self.subTest(set=set_name, call=call["id"]):
                self.assertIs(out["success"], True)
                upstream = [row.get("name") for row in entity_rows(content)]
                client = [row.get("name") for row in entity_rows(out["data"])]
                self.assertEqual(client, upstream)
                if set_name.startswith("A/"):
                    self.assertEqual(set(upstream), {None} if upstream else set())
                else:
                    seen += sum(name is not None for name in upstream)
                for field in replay.COUNT_FIELDS:
                    self.assertEqual(out["data"][field], bare["data"][field])
        self.assertGreaterEqual(seen, 6)

    def test_every_search_warning_is_reviewed(self):
        for (set_name, call, _r, _c), out in zip(self.cases, self.out):
            with self.subTest(set=set_name, call=call["id"]):
                self.assertEqual(out["data"]["upstream_warnings_replaced"], 0)
                self.assertNotIn(replay.REPLACED, json.dumps(out))


class Overview(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        restore_after_class(cls)
        cls.cases = [(s, call, result, result["structuredContent"]) for s, call, result in calls(OVERVIEW)]
        cls.plain = run("9.2.0", [(OVERVIEW, c, r, sc) for _s, c, r, sc in cls.cases])

    def setUp(self):
        self.addCleanup(Guard.restore, Guard().capture())

    def test_ha_mcp_update_is_closed_to_three_keys(self):
        outputs = run("9.2.0", [(OVERVIEW, c, r, with_update(sc)) for _s, c, r, sc in self.cases])
        for (set_name, call, _r, _c), out, plain in zip(self.cases, outputs, self.plain):
            with self.subTest(set=set_name, call=call["id"]):
                self.assertIs(out["success"], True, out.get("details"))
                self.assertEqual(out["data"]["ha_mcp_update"], UPDATE)
                for name in UPDATE_DROPS:
                    self.assertNotIn(name, json.dumps(out))
                self.assertEqual(out["data"]["fields_dropped_by_policy"],
                                 plain["data"]["fields_dropped_by_policy"] + 2)
                self.assertEqual(out["data"]["fields_withheld"], plain["data"]["fields_withheld"])

    def test_another_key_inside_it_is_withheld_and_counted(self):
        outputs = run("9.2.0", [(OVERVIEW, c, r, with_update(sc, {INJECTED: "x"}))
                                for _s, c, r, sc in self.cases])
        for (set_name, call, _r, _c), out, plain in zip(self.cases, outputs, self.plain):
            with self.subTest(set=set_name, call=call["id"]):
                self.assertEqual(out["data"]["ha_mcp_update"], UPDATE)
                self.assertNotIn(INJECTED, json.dumps(out))
                self.assertEqual(out["data"]["fields_withheld"], plain["data"]["fields_withheld"] + 1)

    def test_entity_row_name_is_admitted_and_uncounted(self):
        cases = [(s, c, r, sc) for s, c, r, sc in self.cases if "automation" in sc.get("domain_stats", {})]
        self.assertTrue(cases)
        named = run("9.2.0", [(OVERVIEW, c, r, with_entity_name(sc)) for _s, c, r, sc in cases])
        unnamed = run("9.2.0", [(OVERVIEW, c, r, with_entity_name(sc, None)) for _s, c, r, sc in cases])
        for (set_name, call, _r, _c), out, bare in zip(cases, named, unnamed):
            with self.subTest(set=set_name, call=call["id"]):
                self.assertIs(out["success"], True, out.get("details"))
                self.assertIn(json.dumps(COMPOSED), json.dumps(out["data"]))
                for field in replay.COUNT_FIELDS:
                    self.assertEqual(out["data"][field], bare["data"][field])


class Health(unittest.TestCase):

    def test_injection_leaves_the_result_unchanged_and_raises_the_count(self):
        cases = list(platform_tests.receipts("r20-system-health"))
        self.assertEqual(len(cases), 2)
        for set_name, call, content in cases:
            with self.subTest(set=set_name):
                base = platform_tests.project("9.2.0", HEALTH, content, call["public_arguments"])
                injected = with_update(content)
                out = platform_tests.project("9.2.0", HEALTH, injected, call["public_arguments"])
                self.assertNotIn("ha_mcp_update", json.dumps(out))
                rest = ("fields_withheld",)
                self.assertEqual({k: v for k, v in out.items() if k not in rest},
                                 {k: v for k, v in base.items() if k not in rest})
                p92 = platform_tests.platform_9_2
                new = (p92.names(injected, HEALTH) - p92.names(content, HEALTH)) - p92.KNOWN[HEALTH]
                self.assertGreaterEqual(len(new), 3)
                self.assertEqual(out["fields_withheld"], base["fields_withheld"] + len(new))


if __name__ == "__main__":
    unittest.main()
