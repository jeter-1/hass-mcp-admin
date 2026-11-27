"""HAMCP-501 W6b: native ``ha_search`` entity parity with the component route.

Live ha-mcp 9.2.0 answers ``ha_search`` on the ``ha_mcp_tools`` component
route, so these tests pin native search to that route, from ha-mcp
51e3aac788482a081807591cd5d58d07dc4e540b:

- ``custom_components/ha_mcp_tools/websocket_api/search.py`` (sha256
  5f1a1e312884494245f71c4720c5ddd3ac3d4211f9c8257e9be9460be36781a8): the
  domain, hidden, state and area filters before scoring and paging
  (``_search_entities`` 340-392), the order (176-189), the match types
  (395-472), the registry join (475-527) and the scored texts
  (``_entity_record`` 530-587, texts 548-564);
- ``custom_components/ha_mcp_tools/websocket_api/search_score.py`` (sha256
  ba034030b149091fba1e2777ac6c0c4d9c442fa33148b7d92441cba3feca43de): the tier
  scorer (``_text_tier`` 49-77) and the configuration name tier (103-125);
- ``custom_components/ha_mcp_tools/websocket_api/registry.py`` (sha256
  7a40b10626f042de644aa88c6b06342ecfd525b3d7ac3bbf49dd1bbcbc0cb0bc): the
  composed name (``_composed_entity_name`` 456-467);
- ``src/ha_mcp/tools/search/component.py`` (sha256
  bf14215be89494d531f0876973ffa2056e18b5813e0446fd2b97652dccaad7d3): the
  record, six keys then ``name`` (556-640), and the listing labels (536-553).

The composed name is Core 2026.11.3's (commit
92da9f154079459b9e547126f9bd9fce726de16a):
``homeassistant/helpers/entity_registry.py`` (sha256
a16d82bb68ceb680875c9fd8905d7c6f30d0cd39ed7ee7a12ded2c4a9d565088):
``async_get_full_entity_name`` 663-682, ``_async_get_full_entity_name``
527-630, ``_entity_next_name_part`` 252-260 and ``as_partial_dict`` 393-425;
``homeassistant/helpers/device_registry.py`` (sha256
b5a1ea7b8c622f9dffb1463b954f7899ab1345e260a53079a2574d94dd647f11): the main
device's ``next_name_part`` 735-738 and the child device's 884-889, whose row
carries no manufacturer or model (891-910).

The golden calls B2a to B7 mirror the shape of the beta.13 baseline's reads
(HA Admin, 2026-11-25): the counts, the order and its alphabetical ties, the
score tiers and the match types, without real identifiers. Every entity, name,
state, registry row, device and configuration body in the house is synthetic,
and no test reads live Home Assistant.
The scorer's own goldens are
``docs/evidence/hamcp481/fixtures/component-scoring-golden.json``
(``tests/test_hamcp481_scoring_parity.py``).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT)]

from tests.test_hamcp481_core_reads import EXPECTED_TABLE, entity_row  # noqa: E402
from tests.test_hamcp481_native_search_engine import (  # noqa: E402
    OTHER_INPUTS,
    World,
    area,
    entries_reply,
    floor,
    reasons_of,
    st,
)
from ha_mcp_engineering.errors import HomeAssistantApiError  # noqa: E402
from ha_mcp_engineering.search import core_contracts as c  # noqa: E402
from ha_mcp_engineering.search import entities, reply, scoring  # noqa: E402

# The pinned sources (checked against their clones when written; the
# component's scorer files against the golden's own pins below).
PINS = {
    "custom_components/ha_mcp_tools/websocket_api/search.py":
        "5f1a1e312884494245f71c4720c5ddd3ac3d4211f9c8257e9be9460be36781a8",
    "custom_components/ha_mcp_tools/websocket_api/search_score.py":
        "ba034030b149091fba1e2777ac6c0c4d9c442fa33148b7d92441cba3feca43de",
    "custom_components/ha_mcp_tools/websocket_api/constants.py":
        "672c13bb636c77c549cb27a72cf27c304d8ac1a61cc833348e4531ad40cbfa71",
    "custom_components/ha_mcp_tools/search_locations.py":
        "4567798ee26475efd88e9ed14f9261262e84132d4f453bd6f32789c038c30fd3",
    "custom_components/ha_mcp_tools/websocket_api/registry.py":
        "7a40b10626f042de644aa88c6b06342ecfd525b3d7ac3bbf49dd1bbcbc0cb0bc",
    "src/ha_mcp/tools/search/component.py":
        "bf14215be89494d531f0876973ffa2056e18b5813e0446fd2b97652dccaad7d3",
    "homeassistant/helpers/entity_registry.py":
        "a16d82bb68ceb680875c9fd8905d7c6f30d0cd39ed7ee7a12ded2c4a9d565088",
    "homeassistant/helpers/device_registry.py":
        "b5a1ea7b8c622f9dffb1463b954f7899ab1345e260a53079a2574d94dd647f11",
}
GOLDEN = ROOT / "docs/evidence/hamcp481/fixtures/component-scoring-golden.json"
_PIN_RE = re.compile(r"``([\w./]+\.py)``,?\s*\(?sha256\s+([0-9a-f]{64})")

# The house: (entity_id, state, friendly name, registry values). It mirrors
# the shape of the beta.13 baseline B-reads (the counts, the order and its
# alphabetical ties, the score tiers and the match types) without real
# identifiers: every id, name, area, device and body here is synthetic.
LIGHTS = (
    ("light.amber_group", "on", "Amber Group", {}),
    ("light.birch_spots", "on", "Birch Spots", {"area_id": "patio"}),
    ("light.birch_suite", "on", "Birch Suite", {"area_id": "spare_rooms"}),
    ("light.birch_suite_lamp", "off", "Birch Suite Lamp", {"area_id": "spare_rooms"}),
    ("light.birch_wall_spot_1", "on", "Birch Wall Spot 1", {"area_id": "patio"}),
    ("light.birch_wall_spot_2", "on", "Birch Wall Spot 2", {"area_id": "patio"}),
    # Named by its device (X12): the device part, then the entity name.
    ("light.birch_yard_beam", "off", "Birch Yard Beam",
     {"device_id": "yard_cam", "original_name": "Beam"}),
    ("light.birch_yard_spot", "on", "Birch Yard Spot", {"area_id": "patio"}),
    ("light.cobalt_lamp", "off", "Cobalt Lamp", {"area_id": "east_room"}),
    ("light.fern_spots", "on", "Fern Spots", {}),
    ("light.fern_suite", "on", "Fern Suite", {"area_id": "spare_rooms"}),
    ("light.garnet_spot", "on", "Garnet Spot", {"area_id": "workshop"}),
)
LEAKS = (
    ("binary_sensor.acme_leak_probe_cell", "off", "Acme Leak Probe Cell",
     {"device_id": "leak_probe", "original_name": "Cell"}),
    ("binary_sensor.acme_leak_probe_cell_2", "off", "Acme Leak Probe Cell",
     {"device_id": "leak_probe", "original_name": "Cell"}),
    ("binary_sensor.acme_leak_probe_leak", "off", "Acme Leak Probe Leak",
     {"device_id": "leak_probe", "original_name": "Leak"}),
    ("binary_sensor.spare_leak_probe_problem", "off", "Spare Leak Probe Problem",
     {"device_id": "leak_hub", "original_name": "Problem"}),
)
DOOR = "binary_sensor.north_bath_door_sensor"
# The four automations whose bodies hold DOOR (B5), in id order.
DOOR_AUTOMATIONS = (
    "automation.north_bath_fan_off", "automation.north_bath_fan_on",
    "automation.north_bath_night_light", "automation.north_bath_notify")
OTHERS = (
    (DOOR, "off", "North Bath Door", {"device_id": "door_sensor", "original_name": "Door"}),
    ("binary_sensor.front_door", "off", "Front Door", {}),
    # Filtered out by domain before scoring.
    ("sensor.leak_count", "0", "Leak Count", {}),
    ("switch.light_strip", "on", "Light Strip", {}),
    ("automation.north_bath_fan_off", "on", "North Bath Fan Off", {}),
    ("automation.north_bath_fan_on", "on", "North Bath Fan On", {}),
    ("automation.north_bath_night_light", "on", "North Bath Night Light", {}),
    ("automation.north_bath_notify", "on", "North Bath Notify", {}),
    ("automation.patio_at_dusk", "on", "Patio at dusk", {}),
)
HOUSE = (*LIGHTS, *LEAKS, *OTHERS)
HOUSE_BODIES = {
    "automation.north_bath_fan_off": {
        "id": "fan_off", "alias": "North Bath Fan Off",
        "triggers": [{"trigger": "state", "entity_id": DOOR, "to": "on"}],
        "actions": [{"action": "fan.turn_off", "target": {"entity_id": "fan.north_bath"}}]},
    "automation.north_bath_fan_on": {
        "id": "fan_on", "alias": "North Bath Fan On",
        "triggers": [{"trigger": "state", "entity_id": DOOR, "to": "off"}],
        "actions": [{"action": "fan.turn_on", "target": {"entity_id": "fan.north_bath"}}]},
    "automation.north_bath_night_light": {
        "id": "night_light", "alias": "North Bath Night Light",
        "triggers": [{"trigger": "time", "at": "22:00:00"}],
        "conditions": [{"condition": "state", "entity_id": DOOR, "state": "off"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.cobalt_lamp"}}]},
    "automation.north_bath_notify": {
        "id": "notify", "alias": "North Bath Notify",
        "triggers": [{"trigger": "state", "entity_id": DOOR, "for": "00:10:00"}],
        "actions": [{"action": "persistent_notification.create", "data": {"message": "Door open"}}]},
    "automation.patio_at_dusk": {
        "id": "patio_at_dusk", "alias": "Patio at dusk",
        "triggers": [{"trigger": "sun", "event": "sunset"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.birch_yard_beam"}}]},
}
HOUSE_RELATED = {DOOR: {"automation": ["automation.north_bath_fan_on"],
                        "area": ["north_bath"], "device": ["door_sensor"]}}


def device(device_id, name, area_id=None, parent=None, labels=(), manufacturer=None, model=None,
           name_by_user=None):
    """A Core 2026.11.3 device row: a main device carries manufacturer and
    model; a child device's row has neither (device_registry.py 891-910)."""

    row = {"area_id": area_id, "id": device_id, "labels": list(labels), "name": name,
           "name_by_user": name_by_user, "parent_device_id": parent, "disabled_by": None}
    if parent is None:
        row.update(manufacturer=manufacturer, model=model)
    return row


def label(label_id, name):
    return {"color": None, "created_at": 1.0, "description": None, "icon": None, "label_id": label_id,
            "name": name, "modified_at": 1.0}


def related_reply(related):
    def answer(payloads):
        return [(True, copy.deepcopy(related.get(payload["item_id"], {}))) for payload in payloads]
    return answer


def inventory(rows, areas, floors, labels, devices, related=None, aliases=None):
    aliases = aliases or {}
    return {
        "states": [st(entity_id, value, name) for entity_id, value, name, _ in rows],
        "config/area_registry/list": areas,
        "config/floor_registry/list": floors,
        "config/label_registry/list": labels,
        "config/entity_registry/list": [entity_row(entity_id, **values) for entity_id, _, _, values in rows],
        "config/device_registry/list": devices,
        "input_boolean/list": [],
        **{f"{domain}/list": [] for domain in OTHER_INPUTS},
        "config/entity_registry/get_entries": lambda payload: {
            entity_id: {"aliases": list(aliases.get(entity_id, []))} for entity_id in payload["entity_ids"]},
        "search/related": related_reply(related or {}),
    }


def house():
    # Like the baseline house: no floors and no area aliases (X14).
    return inventory(
        [(entity_id, value, name, {"original_name": name, **values})
         for entity_id, value, name, values in HOUSE],
        [area(area_id, name, [], None, []) for area_id, name in (
            ("east_room", "East Room"), ("north_bath", "North Bath"), ("patio", "Patio"),
            ("spare_rooms", "Spare Rooms"), ("workshop", "Workshop"))],
        [], [label("exterior", "Exterior")],
        [device("yard_cam", "Birch Yard Camera", "patio", manufacturer="Kestrel", model="KC-20"),
         device("leak_probe", "Acme Leak Probe", "north_bath", manufacturer="Acme", model="LP-10"),
         device("door_sensor", "North Bath Door Sensor", "north_bath", manufacturer="Acme",
                model="DS-10"),
         device("leak_hub", "Spare Leak Probe", "workshop", manufacturer="Generic", model="WL-1")],
        HOUSE_RELATED)


# The synthetic world of the P-cases.
SYNTHETIC = (
    # Found by its own label only; a name of "" wins over original_name.
    ("sensor.own_label", "1", "Own label sensor",
     {"labels": ["lbl01"], "name": "", "original_name": "Ignored"}),
    # On the main device hub: the device's label, manufacturer and model.
    ("sensor.device_label", "2", "Device label sensor", {"device_id": "hub", "original_name": "Status"}),
    # On the child of hub: parent, device and entity name; no manufacturer.
    ("sensor.child_reading", "3", "Child reading", {"device_id": "child", "original_name": "Reading"}),
    # Its own area overrides the device's; the next name part is the area.
    ("switch.desk", "off", "Desk switch",
     {"area_id": "study", "device_id": "plug", "original_name": "Desk switch"}),
    # The device's area; name_by_user wins over the device name.
    ("light.lounge_lamp", "on", "Lounge lamp", {"device_id": "plug", "original_name": "Lamp"}),
    ("light.empty_name", "on", "Empty name light",
     {"device_id": "plug", "name": "", "original_name": "Ignored"}),
    ("light.hidden_lamp", "off", "Hidden lamp", {"area_id": "lounge", "hidden_by": "user"}),
    # The fuzzy boundary (component-scoring-golden.json ratios: 70 and 69).
    ("sensor.p1", "4", "abcdefgxyz", {"original_name": None}),
    ("sensor.p2", "5", "abcdefghixyzw", {"original_name": None}),
)
SYNTHETIC_ALIASES = {"light.lounge_lamp": ["Reading light"]}


def synthetic():
    return inventory(
        SYNTHETIC,
        [area("study", "Study", ["Office"], "upper", []), area("lounge", "Lounge", [], "lower", [])],
        [floor("upper", "Upper Floor", 1), floor("lower", "Lower Floor", 0)],
        # Label ids unlike their names: a named label scores by name.
        [label("lbl01", "Vacation"), label("lbl02", "Security")],
        [device("hub", "Hub", "study", labels=["lbl02"], manufacturer="Acme", model="HB-1"),
         device("child", "Child Node", parent="hub"),
         device("plug", "Plug", "lounge", manufacturer="Zeta", model="ZP-9", name_by_user="Desk Plug")],
        aliases=SYNTHETIC_ALIASES)


def ids(result):
    return [record["entity_id"] for record in result["entities"]]


def scored(result):
    return [(record["entity_id"], record["score"], record["match_type"]) for record in result["entities"]]


class House(World):
    def setUp(self):
        super().setUp()
        self.values.clear()
        self.values.update(house())
        # S1 b+: the synthetic delegated route matches these bodies; native
        # search reads none.
        self.bodies = copy.deepcopy(HOUSE_BODIES)


class GoldenTests(House):
    """The shape of the beta.13 baseline reads B2a to B7, on the synthetic
    house, as the component route answers them."""

    LIGHT_IDS = [entity_id for entity_id, *_ in LIGHTS]
    LEAK_IDS = [entity_id for entity_id, *_ in LEAKS]

    def names(self, result):
        return [record["name"] for record in result["entities"]]

    async def test_b2a_domain_listing(self):
        result = await self.go(domain_filter="light")
        self.assertEqual(result["search_type"], "domain_listing")
        self.assertEqual(scored(result), [(entity_id, 100, "domain_listing") for entity_id in self.LIGHT_IDS[:10]])
        self.assertEqual((result["entity_total_matches"], result["entity_has_more"], result["entity_next_offset"]),
                         (12, True, 10))
        # The composed name (X12): the device part for the device-named light.
        self.assertEqual(self.names(result), [
            "Amber Group", "Birch Spots", "Birch Suite", "Birch Suite Lamp", "Birch Wall Spot 1",
            "Birch Wall Spot 2", "Birch Yard Camera Beam", "Birch Yard Spot", "Cobalt Lamp",
            "Fern Spots"])
        self.assertEqual(list(result["entities"][0]), [*entities.RECORD_KEYS, "name"])
        self.assertNotIn("area_registry", self.reads[-1])
        self.assertNotIn("entity_registry_entries", self.reads[-1])

    async def test_b3a_and_b4a_light_query_in_the_light_domain(self):
        for exact_match, match_type, search_type in ((True, "exact_match", "exact_match"),
                                                     (False, "exact_domain", "fuzzy_search")):
            with self.subTest(exact_match=exact_match):
                result = await self.go(query="light", domain_filter="light", exact_match=exact_match)
                self.assertEqual(result["search_type"], search_type)
                # The listing's order and a flat substring tier of 80.
                self.assertEqual(scored(result), [(entity_id, 80, match_type)
                                                  for entity_id in self.LIGHT_IDS[:10]])
                self.assertEqual(result["entity_total_matches"], 12)
                # X1 kept: a domain filter skips the configuration search.
                self.assertFalse(result["applied"]["config_search_ran"])
                self.assertEqual(result["warnings"], [reply.WARNING_CONFIG_SKIPPED])
                rest = await self.go(query="light", domain_filter="light", exact_match=exact_match, offset=10)
                self.assertEqual(ids(rest), self.LIGHT_IDS[10:])

    async def test_b3b_and_b4b_leak_query_in_the_binary_sensor_domain(self):
        for exact_match, match_type in ((True, "exact_match"), (False, "partial_id")):
            with self.subTest(exact_match=exact_match):
                result = await self.go(query="leak", domain_filter="binary_sensor", exact_match=exact_match)
                self.assertEqual(scored(result), [(entity_id, 80, match_type) for entity_id in self.LEAK_IDS])
                self.assertEqual(result["entity_total_matches"], 4)
                self.assertNotIn("sensor.leak_count", ids(result))

    async def test_b5_an_entity_id_query(self):
        result = await self.go(query=DOOR)
        self.assertEqual(result["entities"], [{
            "entity_id": DOOR, "friendly_name": "North Bath Door", "domain": "binary_sensor",
            "state": "off", "score": 100, "match_type": "exact_match",
            "name": "North Bath Door Sensor Door"}])
        # The automations whose bodies hold the id, as the route matched
        # them (S1 b+). match_in_references is native's graph read
        # ("improved"; live serves false).
        self.assertEqual([(r["entity_id"], r["score"], r["match_in_name"], r["match_in_config"],
                           r["match_in_references"]) for r in result["automations"]], [
            (entity_id, 100, False, True, entity_id == "automation.north_bath_fan_on")
            for entity_id in DOOR_AUTOMATIONS])
        self.assertEqual(result["config_total_matches"], 4)

    async def test_b6c_an_unknown_area(self):
        # X6 kept: success, no rows and no notice.
        result = await self.go(area_filter="Loft")
        self.assertEqual((result["entities"], result["entity_total_matches"]), ([], 0))
        self.assertEqual(result["search_type"], "area_only")
        self.assertNotIn("warnings", result)
        self.assertEqual(result["partial_reasons"], [])

    async def test_b7_the_state_filter_runs_before_paging(self):
        on = [entity_id for entity_id, value, *_ in LIGHTS if value == "on"]
        result = await self.go(state_filter="on", domain_filter="light", limit=5)
        self.assertEqual(scored(result), [(entity_id, 100, "domain_listing") for entity_id in on[:5]])
        self.assertEqual((result["entity_total_matches"], result["entity_next_offset"]), (len(on), 5))
        self.assertEqual(result["fields_withheld"], 0)  # no state_filter_note
        # P5: a query filters by state before paging too.
        for exact_match in (True, False):
            with self.subTest(exact_match=exact_match):
                query = await self.go(query="light", state_filter="on", domain_filter="light", limit=5,
                                      exact_match=exact_match)
                self.assertEqual(ids(query), on[:5])
                self.assertEqual(query["entity_total_matches"], len(on))
                self.assertEqual(query["warnings"], [reply.WARNING_CONFIG_SKIPPED])

    async def test_the_area_modes_on_the_house(self):
        # B6b-shaped: an area name lists its entities, own or device area.
        result = await self.go(area_filter="North Bath", limit=50)
        self.assertEqual(ids(result), [*self.LEAK_IDS[:3], DOOR])
        self.assertEqual({r["match_type"] for r in result["entities"]}, {"area_match"})
        query = await self.go(area_filter="north_bath", query="cell")
        self.assertEqual(scored(query), [(entity_id, 80, "exact_match") for entity_id in self.LEAK_IDS[:2]])


class StatesOnlyAreaTests(House):
    """HAMCP-522: the area modes list states only, as the component route
    does (``_search_entities`` 359-371 iterates ``_iter_states``; a state with
    no registry row has no area, ``_registry_enrichment`` 500)."""

    def setUp(self):
        super().setUp()
        rows = self.values["config/entity_registry/list"]
        # In North Bath by its own area, disabled: a registry row, no state.
        rows.append(entity_row("sensor.north_bath_humidity", area_id="north_bath",
                               original_name="Humidity", disabled_by="user"))
        # In North Bath by its device, enabled but with no state row.
        rows.append(entity_row("binary_sensor.acme_leak_probe_battery", device_id="leak_probe",
                               original_name="Battery"))
        # A state with no registry row: it has no area.
        self.values["states"].append(st("sensor.north_bath_stray", "1", "North Bath stray"))

    NO_STATE = ("sensor.north_bath_humidity", "binary_sensor.acme_leak_probe_battery")

    async def test_area_only_lists_and_counts_states_only(self):
        result = await self.go(area_filter="North Bath", limit=50)
        self.assertEqual(result["search_type"], "area_only")
        self.assertEqual(ids(result), [*GoldenTests.LEAK_IDS[:3], DOOR])
        self.assertEqual(result["entity_total_matches"], 4)
        self.assertEqual(result["partial_reasons"], [])

    async def test_area_filtered_query_scores_states_only(self):
        for query, expected in (("humidity", []), ("battery", []), ("stray", []),
                                ("acme leak probe", GoldenTests.LEAK_IDS[:3])):
            with self.subTest(query=query):
                result = await self.go(area_filter="north_bath", query=query, limit=50)
                self.assertEqual(result["search_type"], "area_filtered_query")
                self.assertEqual(sorted(ids(result)), sorted(expected))
                self.assertEqual(result["entity_total_matches"], len(expected))

    async def test_no_unknown_state_is_made_up(self):
        # Before HAMCP-522 a registry row with no state was listed as "unknown".
        result = await self.go(area_filter="North Bath", state_filter="unknown", limit=50)
        self.assertEqual((ids(result), result["entity_total_matches"]), ([], 0))

    async def test_the_other_modes_are_unchanged(self):
        # A query with no area scores the states, so the stray state is found
        # and the registry rows with no state are not.
        found = ids(await self.go(query="north bath stray", search_types=None, limit=50))
        self.assertIn("sensor.north_bath_stray", found)
        listing = ids(await self.go(domain_filter="sensor", limit=50))
        self.assertIn("sensor.north_bath_stray", listing)
        for entity_id in self.NO_STATE:
            self.assertNotIn(entity_id, found)
            self.assertNotIn(entity_id, listing)


class Synthetic(World):
    def setUp(self):
        super().setUp()
        self.values.clear()
        self.values.update(synthetic())


class TextTests(Synthetic):
    """P2 and P7: the scored texts (search.py 548-564)."""

    async def query(self, text, **arguments):
        return await self.go(query=text, search_types=None, limit=50, **arguments)

    async def test_labels_from_the_entity_and_from_its_device(self):
        self.assertEqual(scored(await self.query("vacation")), [("sensor.own_label", 100, "exact_match")])
        # The device's label; the child of that device does not inherit it.
        self.assertEqual(scored(await self.query("security")), [("sensor.device_label", 100, "exact_match")])
        # A named label's id is not a text; a label with no registry row
        # scores by its id (registry.py 362-368).
        for text in ("lbl01", "lbl02"):
            with self.subTest(text=text):
                self.assertEqual(ids(await self.query(text)), [])
        self.values["config/entity_registry/list"][0]["labels"].append("orphan_tag")
        self.assertEqual(scored(await self.query("orphan_tag")), [("sensor.own_label", 100, "exact_match")])

    async def test_manufacturer_and_model_of_the_main_device_only(self):
        for text in ("acme", "hb-1", "HB"):
            with self.subTest(text=text):
                self.assertEqual(ids(await self.query(text)), ["sensor.device_label"])
        self.assertEqual(ids(await self.query("zeta")), ["light.empty_name", "light.lounge_lamp", "switch.desk"])

    async def test_area_and_floor_names(self):
        # Study: switch.desk's own area, hub's area, and the child's through
        # its parent. Not light.lounge_lamp, whose device is in the lounge.
        study = ["sensor.child_reading", "sensor.device_label", "switch.desk"]
        self.assertEqual(ids(await self.query("study")), study)
        self.assertEqual(ids(await self.query("upper floor")), study)
        # switch.desk's own area wins over its device's lounge. The hidden
        # lamp's 100 is 80 after the penalty, so it sorts last.
        for text in ("lounge", "lower floor"):
            with self.subTest(text=text):
                self.assertEqual(scored(await self.query(text)), [
                    ("light.empty_name", 100, "exact_match"), ("light.lounge_lamp", 100, "exact_match"),
                    ("light.hidden_lamp", 80, "exact_match")])
        # An area or floor alias is not a scored text (search.py 548-564).
        self.values["config/floor_registry/list"][0]["aliases"] = ["Loft"]
        for text in ("office", "loft"):
            with self.subTest(text=text):
                self.assertEqual(ids(await self.query(text)), [])

    async def test_the_device_name_by_user_is_a_text(self):
        # switch.desk is named by its area, so "Desk Plug" is in no name of
        # it: only its device's name_by_user text finds it.
        result = await self.query("desk plug")
        self.assertIn(("switch.desk", 100, "exact_match"), scored(result))
        self.assertEqual(ids(await self.query("plug")), ["light.empty_name", "light.lounge_lamp", "switch.desk"])

    async def test_the_bare_domain_is_not_a_text(self):
        result = await self.query("switch")
        self.assertEqual(scored(result), [("switch.desk", 80, "exact_match")])
        fuzzy = await self.query("switch", exact_match=False)
        self.assertEqual(scored(fuzzy), [("switch.desk", 80, "exact_domain")])

    async def test_an_alias_is_a_text_and_drives_the_match_type(self):
        self.assertEqual(scored(await self.query("reading light")), [("light.lounge_lamp", 100, "exact_match")])
        fuzzy = await self.query("reading", exact_match=False)
        self.assertEqual(scored(fuzzy), [("light.lounge_lamp", 80, "alias_match"),
                                         ("sensor.child_reading", 80, "partial_id")])


class NameTests(Synthetic):
    """P1: the composed ``name`` (Core 2026.11.3 ``async_get_full_entity_name``)."""

    async def names(self, **arguments):
        result = await self.go(**arguments, limit=50)
        return {record["entity_id"]: record["name"] for record in result["entities"]}

    async def test_composed_names(self):
        names = {}
        for listing in ({"domain_filter": "sensor"}, {"domain_filter": "light"}, {"domain_filter": "switch"}):
            names.update(await self.names(**listing))
        self.assertEqual(names, {
            # A name of "" and no device: no part, so the friendly name.
            "sensor.own_label": "Own label sensor",
            # A main device: the device part, then the entity name.
            "sensor.device_label": "Hub Status",
            # A child device whose next part is its parent.
            "sensor.child_reading": "Hub Child Node Reading",
            # The entity's next part is its area: no device part.
            "switch.desk": "Desk switch",
            # name_by_user wins over the device name.
            "light.lounge_lamp": "Desk Plug Lamp",
            # A name of "" wins over original_name.
            "light.empty_name": "Desk Plug",
            "light.hidden_lamp": "Light.Hidden_Lamp",
            # No name and no original name: the friendly name.
            "sensor.p1": "abcdefgxyz",
            "sensor.p2": "abcdefghixyzw",
        })

    async def test_rows_without_a_wire_next_name_part(self):
        # Core rows that carry no next_name_part: the derived part names them.
        expected = await self.names(domain_filter="sensor")
        for row in self.values["config/entity_registry/list"]:
            del row["next_name_part"]
        self.assertEqual(await self.names(domain_filter="sensor"), expected)
        self.assertEqual(expected["sensor.child_reading"], "Hub Child Node Reading")

    async def test_original_name_is_not_scored_when_name_is_set(self):
        self.assertEqual(ids(await self.go(query="ignored", search_types=None, exact_match=False)), [])

    async def test_a_parent_name_by_user_names_the_child(self):
        self.values["config/device_registry/list"][0]["name_by_user"] = "Main Hub"
        names = await self.names(domain_filter="sensor")
        self.assertEqual((names["sensor.device_label"], names["sensor.child_reading"]),
                         ("Main Hub Status", "Main Hub Child Node Reading"))

    async def test_a_child_whose_parent_is_a_child_has_no_parent_part(self):
        self.values["config/device_registry/list"].append(device("grandchild", "Leaf", parent="child"))
        self.values["config/entity_registry/list"].append(
            entity_row("sensor.leaf", device_id="grandchild", original_name="Value"))
        self.values["states"].append(st("sensor.leaf", "6", "Leaf value"))
        names = await self.names(domain_filter="sensor")
        self.assertEqual(names["sensor.leaf"], "Leaf Value")

    async def test_a_child_with_an_area_has_no_parent_part(self):
        self.values["config/device_registry/list"][1]["area_id"] = "lounge"
        names = await self.names(domain_filter="sensor")
        self.assertEqual(names["sensor.child_reading"], "Child Node Reading")


class TierTests(Synthetic):
    """P3, P4 and P6: the tier scorer, the hidden penalty and match types."""

    async def test_the_hidden_penalty(self):
        self.assertEqual(scored(await self.go(query="light.hidden_lamp", search_types=None)),
                         [("light.hidden_lamp", 80, "exact_match")])
        self.assertEqual(scored(await self.go(query="hidden_lamp", search_types=None)),
                         [("light.hidden_lamp", 60, "exact_match")])
        listing = await self.go(area_filter="Lounge", include_hidden=True)
        self.assertIn(("light.hidden_lamp", 80, "area_match"), scored(listing))
        for arguments in ({"query": "light.hidden_lamp"}, {"query": "hidden", "exact_match": False},
                          {"area_filter": "Lounge"}, {"domain_filter": "light"}):
            with self.subTest(arguments=arguments):
                result = await self.go(**arguments, include_hidden=False)
                self.assertNotIn("light.hidden_lamp", ids(result))

    async def test_the_state_filter_ignores_the_state_case(self):
        # P5: both sides lowered (search.py 340-392).
        self.values["config/entity_registry/list"].append(entity_row("sensor.mode", original_name="Mode"))
        self.values["states"].append(st("sensor.mode", "Away", "House mode"))
        for state_filter in ("away", "AWAY", " Away "):
            with self.subTest(state_filter=state_filter):
                listing = await self.go(domain_filter="sensor", state_filter=state_filter)
                self.assertEqual(scored(listing), [("sensor.mode", 100, "domain_listing")])
                query = await self.go(query="house mode", state_filter=state_filter, search_types=None)
                self.assertEqual(scored(query), [("sensor.mode", 100, "exact_match")])
        self.assertEqual(ids(await self.go(domain_filter="sensor", state_filter="home")), [])

    async def test_the_fuzzy_ratio_boundary(self):
        # component-scoring-golden.json: ratio 70 is kept, 69 is skipped.
        self.assertEqual(scoring.ratio("abcdefghij", "abcdefgxyz"), 70)
        self.assertEqual(scoring.ratio("abcdefghiklmn", "abcdefghixyzw"), 69)
        kept = await self.go(query="abcdefghij", exact_match=False, search_types=None)
        self.assertIn(("sensor.p1", 70, "fuzzy_match"), scored(kept))
        skipped = await self.go(query="abcdefghiklmn", exact_match=False, search_types=None)
        self.assertNotIn("sensor.p2", ids(skipped))
        # Exact mode never uses the ratio.
        exact = await self.go(query="abcdefghij", search_types=None)
        self.assertEqual(ids(exact), [])

    async def test_match_types(self):
        # The score is the best text's tier; the match type reads the
        # names only, so the device name "Child Node" scores 100 while the
        # composed name "Hub Child Node Reading" makes it partial_name.
        cases = (("sensor.p1", "exact_id", 100), ("desk switch", "exact_name", 100),
                 ("desk plug lamp", "exact_name", 100), ("p1", "partial_id", 80),
                 ("child node", "partial_name", 100), ("node read", "partial_name", 80),
                 # The friendly name is a name: the composed one is "Hub Status".
                 ("device label sensor", "exact_name", 100))
        for query, match_type, score in cases:
            with self.subTest(query=query):
                result = await self.go(query=query, exact_match=False, search_types=None)
                self.assertEqual(scored(result)[0][1:], (score, match_type))
        # No typo fallback: a misspelling under the ratio threshold matches nothing.
        self.assertEqual(ids(await self.go(query="xyzzy", exact_match=False, search_types=None)), [])

    async def test_the_area_filtered_query_scores_like_a_query(self):
        # P6: the tier scorer, not a separate area threshold.
        exact = await self.go(area_filter="Study", query="hub")
        self.assertEqual(exact["search_type"], "area_filtered_query")
        self.assertEqual(scored(exact), [("sensor.device_label", 100, "exact_match"),
                                         ("sensor.child_reading", 80, "exact_match")])
        # The device name "Hub" scores 100; the names make it partial_name.
        fuzzy = await self.go(area_filter="upper floor", query="hub", exact_match=False)
        self.assertEqual(fuzzy["search_type"], "area_filtered_query")
        self.assertEqual(scored(fuzzy), [("sensor.device_label", 100, "partial_name"),
                                         ("sensor.child_reading", 80, "partial_name")])


class ConfigNameTests(World):
    """P8, the configuration name tier (search_score.py 103-125), as W6's
    configuration search scores it: over the native storage helpers. Under
    S1 b+ an automation, script or scene name is the delegated route's."""

    async def test_separator_normalized_names(self):
        fuzzy = await self.go(query="guest-mode", search_types=["helper"], exact_match=False)
        self.assertEqual([(r["entity_id"], r["score"]) for r in fuzzy["helpers"] if r["match_in_name"]],
                         [("input_boolean.guest_mode", 100)])
        exact = await self.go(query="guest-mode", search_types=["helper"])
        self.assertEqual(exact["helpers"], [])


class DriftTests(Synthetic):
    """Fail-closed: the registries the scored texts read must be usable."""

    async def assertRefused(self, source, arguments=None):
        error = await self.refused("source_unavailable", arguments or {"query": "hub", "search_types": None})
        self.assertEqual(error.details, {"reason": "source_unavailable", "source": source})

    async def test_an_unusable_device_registry(self):
        self.values["config/device_registry/list"] = HomeAssistantApiError()
        await self.assertRefused("device_registry")
        await self.assertRefused("device_registry", {"area_filter": "Study"})
        # A listing serves a null name and discloses it.
        listing = await self.go(domain_filter="sensor")
        self.assertEqual({record["name"] for record in listing["entities"]}, {None})
        self.assertEqual(reasons_of(listing), {("source_unavailable", "entity", "device_registry"): 5})

    async def test_drifted_device_texts(self):
        rows = self.values["config/device_registry/list"]
        for field_name in ("manufacturer", "model", "name", "name_by_user", "labels"):
            with self.subTest(field=field_name):
                self.values["config/device_registry/list"] = copy.deepcopy(rows)
                self.values["config/device_registry/list"][0][field_name] = 7
                await self.assertRefused("device_registry")
        # A listing reads no manufacturer or model.
        self.values["config/device_registry/list"] = copy.deepcopy(rows)
        self.values["config/device_registry/list"][0]["model"] = 7
        listing = await self.go(domain_filter="sensor", limit=50)
        self.assertEqual(reasons_of(listing), {})
        self.assertIn("Hub Status", [record["name"] for record in listing["entities"]])

    async def test_an_absent_manufacturer_is_not_drift(self):
        for row in self.values["config/device_registry/list"]:
            row.pop("manufacturer", None)
            row.pop("model", None)
        self.assertEqual(ids(await self.go(query="hub", search_types=None)),
                         ["sensor.device_label", "sensor.child_reading"])
        self.assertEqual(ids(await self.go(query="acme", search_types=None)), [])

    async def test_duplicate_or_dropped_device_rows(self):
        rows = self.values["config/device_registry/list"]
        for extra in (copy.deepcopy(rows[0]), "not a row", {"id": 7}):
            with self.subTest(extra=extra):
                self.values["config/device_registry/list"] = [*copy.deepcopy(rows), extra]
                await self.assertRefused("device_registry")

    async def test_a_wire_next_name_part_must_equal_the_derived_one(self):
        entity_rows = self.values["config/entity_registry/list"]
        next(row for row in entity_rows if row["entity_id"] == "switch.desk")["next_name_part"] = "device"
        await self.assertRefused("entity_registry")
        await self.assertRefused("entity_registry", {"domain_filter": "switch"})
        self.values.update(synthetic())
        self.values["config/device_registry/list"][1]["next_name_part"] = "area"
        await self.assertRefused("device_registry")
        listing = await self.go(domain_filter="sensor")
        self.assertEqual({record["name"] for record in listing["entities"]}, {None})
        # Core's own values pass, and a non-string value is drift.
        self.values.update(synthetic())
        for row, part in zip(self.values["config/device_registry/list"], ("area", "parent_device", "area")):
            row["next_name_part"] = part
        self.assertEqual(ids(await self.go(query="hub", search_types=None)),
                         ["sensor.device_label", "sensor.child_reading"])
        self.values["config/device_registry/list"][0]["next_name_part"] = 7
        await self.assertRefused("device_registry")

    async def test_unusable_label_floor_and_area_registries(self):
        for command, source in (("config/label_registry/list", "label_registry"),
                                ("config/floor_registry/list", "floor_registry"),
                                ("config/area_registry/list", "area_registry")):
            with self.subTest(source=source):
                self.values.update(synthetic())
                self.values[command] = HomeAssistantApiError()
                await self.assertRefused(source)
                # A listing does not read them.
                await self.go(domain_filter="sensor")

    async def test_a_drifted_original_name(self):
        self.values["config/entity_registry/list"][1]["original_name"] = 7
        await self.assertRefused("entity_registry")
        await self.assertRefused("entity_registry", {"domain_filter": "sensor"})

    async def test_drifted_area_floor_and_label_fields_fail_a_query(self):
        for command, field_name, source in (("config/area_registry/list", "name", "area_registry"),
                                            ("config/area_registry/list", "floor_id", "area_registry"),
                                            ("config/floor_registry/list", "name", "floor_registry"),
                                            ("config/label_registry/list", "name", "label_registry")):
            with self.subTest(source=source, field=field_name):
                self.values.update(synthetic())
                self.values[command][0][field_name] = 7
                await self.assertRefused(source)
                # A listing does not read them.
                await self.go(domain_filter="sensor")

    async def test_drifted_entity_labels_fail_a_query(self):
        self.values["config/entity_registry/list"][0]["labels"] = 7
        await self.assertRefused("entity_registry")
        await self.go(domain_filter="sensor")


class ContractTests(unittest.TestCase):
    """No new read; the device rows carry the optional component texts."""

    def test_the_reads_are_unchanged(self):
        self.assertEqual(set(c.READS), set(EXPECTED_TABLE))
        self.assertEqual(c.READS["device_registry"].requirement, "core.direct_device_registry_read")

    def test_registry_row_fields(self):
        self.assertEqual(dict(c.ROW_FIELDS["device_registry"]), {
            "name": c.OPTIONAL_STR, "name_by_user": c.OPTIONAL_STR, "area_id": c.OPTIONAL_STR,
            "parent_device_id": c.OPTIONAL_STR, "labels": c.STR_LIST, "disabled_by": c.OPTIONAL_STR,
            "manufacturer": c.OPTIONAL_STR, "model": c.OPTIONAL_STR, "next_name_part": c.OPTIONAL_STR})
        self.assertEqual(c.ROW_FIELDS["entity_registry"]["next_name_part"], c.OPTIONAL_STR)
        self.assertEqual(dict(c.OPTIONAL_FIELDS), {
            "entity_registry": frozenset({"has_entity_name", "next_name_part"}),
            "device_registry": frozenset({"manufacturer", "model", "next_name_part"})})

    def test_the_cited_pins_agree(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["source"]
        self.assertEqual(golden["commit"], "51e3aac788482a081807591cd5d58d07dc4e540b")
        self.assertEqual({path: PINS[path] for path in golden["sha256"]}, golden["sha256"])
        cited = {}
        for module in (sys.modules[__name__], entities, scoring):
            for path, digest in _PIN_RE.findall(module.__doc__):
                if path in PINS:
                    cited.setdefault(path, set()).add(digest)
        self.assertEqual(cited, {path: {digest} for path, digest in PINS.items()})

    def test_the_retired_warnings_are_gone(self):
        self.assertEqual(reply.WARNINGS, (reply.WARNING_ENTITY_SKIPPED, reply.WARNING_CONFIG_SKIPPED,
                                          reply.WARNING_AMBIGUOUS_FLOOR))
        self.assertFalse(hasattr(reply, "WARNING_STATE_PAGE_ONLY"))
        self.assertFalse(hasattr(reply, "WARNING_COMPOSED_NAME"))
        # HAMCP-520: every mode filters by state before paging, so the
        # page-filtered mode set and its withheld note are gone.
        self.assertFalse(hasattr(entities, "PAGE_FILTERED_MODES"))


if __name__ == "__main__":
    unittest.main()
