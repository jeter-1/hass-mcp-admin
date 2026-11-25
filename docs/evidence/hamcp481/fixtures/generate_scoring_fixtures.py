"""HAMCP-481 W1: golden scores from the pinned ha-mcp 9.2.0 scorers.

Loads exactly two upstream files by path, after checking their SHA-256:
``src/ha_mcp/utils/fuzzy_search.py`` (standard library only) and
``src/ha_mcp/tools/smart_search/_scoring.py``. ``_scoring.py`` runs against a
stub ``_base`` whose ``_SearchBase`` carries only the ``settings`` attribute
the scoring mixin reads; the real ``_base`` imports the REST client and is
not loaded. Package parents are empty placeholder modules, so no other
upstream file can be imported. Nothing is edited, nothing contacts a network
or Home Assistant, and every input is the synthetic corpus below.

The output records the inputs and the upstream outputs side by side, and
``tests/test_hamcp481_scoring_parity.py`` checks the Engineering port
(``search/scoring.py``) against it.

Usage (from a Python with no packages installed, outside the upstream tree,
in a network namespace with no network; a source tree of ha-mcp at commit
51e3aac7 holds both files)::

    unshare -rn <venv>/bin/python -I docs/evidence/hamcp481/fixtures/generate_scoring_fixtures.py \
        --upstream <ha-mcp-51e3aac7> \
        --output-dir docs/evidence/hamcp481/fixtures

``--check`` regenerates in memory and fails when a committed file differs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

SOURCE_REPOSITORY = "https://github.com/homeassistant-ai/ha-mcp"
SOURCE_VERSION = "9.2.0"
SOURCE_COMMIT = "51e3aac788482a081807591cd5d58d07dc4e540b"
FUZZY_PATH = "src/ha_mcp/utils/fuzzy_search.py"
SCORING_PATH = "src/ha_mcp/tools/smart_search/_scoring.py"
SOURCE_SHA256 = {
    FUZZY_PATH: "6759bdd4223bf3221456be84250b23e67d87dd1a783660eb2dbd3f8bee54a207",
    SCORING_PATH: "09232b092f2cc41aa2d143f5b5bacfbca6952b22d339d52984b694b83cb73682",
}
GOLDEN_NAME = "scoring-golden.json"
PROVENANCE_NAME = "provenance.json"
COMMAND = (
    "unshare -rn <venv>/bin/python -I docs/evidence/hamcp481/fixtures/generate_scoring_fixtures.py"
    " --upstream <ha-mcp-51e3aac7> --output-dir docs/evidence/hamcp481/fixtures"
)


def entity(entity_id, friendly_name=None, aliases=None, hidden_by=None, attributes=True):
    record = {"entity_id": entity_id, "state": "on"}
    if attributes:
        record["attributes"] = {} if friendly_name is None else {"friendly_name": friendly_name}
    if aliases is not None:
        record["_aliases"] = aliases
    if hidden_by is not None:
        record["_hidden_by"] = hidden_by
    return record


# Enriched state records, as 9.2.0's smart entity search hands them to the
# searcher: ``_aliases`` from the entity registry, ``_hidden_by`` when hidden.
ENTITIES = (
    entity("light.bed_light", "Bed Light"),
    entity("light.kitchen_ceiling", "Kitchen Ceiling", aliases=["Cooking lamp"]),
    entity("light.living_room_lamp", "Living Room Lamp"),
    entity("switch.garage_door", "Garage Door Opener"),
    entity("sensor.garden_temperature", "Garden Temperature"),
    entity("sensor.kitchen_temperature", "Kitchen Temperature",
           aliases=["Cooking thermometer"], hidden_by="user"),
    entity("binary_sensor.front_door", "Front Door", aliases=["Main entrance", 7]),
    entity("climate.bedroom_thermostat", "Bedroom Thermostat"),
    entity("cover.living_room_blinds", "Living Room Blinds"),
    entity("light.hallway", "Hallway", hidden_by="integration"),
    entity("fan.bedroom_fan", "Bedroom Fan"),
    entity("media_player.living_room_tv", "Living Room TV"),
    entity("sensor.outdoor_humidity", "Outdoor Humidity"),
    entity("light.salon_lumiere", "Lumière Salon"),
    entity("input_boolean.vacation_mode", "Vacation Mode"),
    entity("script.morning_routine", "Morning Routine"),
    entity("automation.lights_off_at_night", "Lights off at night"),
    entity("lock.front_door_lock", "Front Door Lock"),
    entity("switch.coffee_maker", "Coffee Maker", aliases=["Espresso"]),
    entity("sensor.bed_light_power", "Bed Light Power"),
    entity("sensor.uptime"),
    entity("light.porch", "Porch Light", hidden_by=""),
    entity("button.restart_router", attributes=False),
)

ENTITY_QUERIES = (
    "bed light", "bedlight", "kitchen", "kitchen temperature", "light",
    "light.bed_light", "Bed Light", "cooking", "espresso", "main entrance",
    "temprature", "kitchn", "garbage truck schedule", "lit", "thermostat bedroom",
    "living room", "door", "front door", "xyzzy", "", "  ", "lumière", "sensor",
    "living-room tv", "vacation", "TV", "sensor.uptime", "porch", "hallway",
    "uptime", "kitchen xyzzy", "bedrom thermostat", "bedrom thermostt",
    "garaje dor opner", "kitchn qqqq wwww", "7", "restart router", "  Bed  ",
    # A raw score exactly at a threshold (80 and 60) passes the gate.
    "door blinds", "lamp sensor",
)
ENTITY_THRESHOLDS = (60, 80)
# (query, threshold, limit, offset): the source pages after sorting.
PAGED = (("light", 60, 2, 1), ("living room", 60, 3, 0), ("door", 80, 1, 5))

# (entity_id, friendly_name, domain, query) as 9.2.0's configuration search
# passes them: automation and script records score under their own domain,
# helpers under the helper type.
NAME_SCORES = (
    ("automation.lights_off_at_night", "Lights off at night", "automation", "lights"),
    ("automation.lights_off_at_night", "Lights off at night", "automation", "lights off at night"),
    ("automation.lights_off_at_night", "Lights off at night", "automation", "automation"),
    ("automation.lights_off_at_night", "Lights off at night", "automation", "light"),
    ("automation.lights_off_at_night", "Lights off at night", "automation", "nite lights"),
    ("script.morning_routine", "Morning Routine", "script", "morning"),
    ("script.morning_routine", "Morning Routine", "script", "script.morning_routine"),
    ("script.morning_routine", "Morning Routine", "script", "routine morning"),
    ("script.morning_routine", "Morning Routine", "script", "xyzzy"),
    ("input_boolean.vacation_mode", "Vacation Mode", "input_boolean", "vacation"),
    ("input_boolean.vacation_mode", "Vacation Mode", "input_boolean", "input_boolean"),
    ("input_boolean.vacation_mode", "vacation_mode", "input_boolean", "vacation mode"),
    ("scene.movie_night", "Movie night", "scene", "movie"),
    ("automation.salon_lumiere", "Salon lumiere", "automation", "salon lumiere"),
    ("automation.kitchen_light", "Kitchen light switch", "automation", "kitchen light"),
    ("automation.bedroom_sensor", "Bedroom climate sensor", "automation", "bedroom sensor climate"),
    ("automation.x", "", "automation", "x"),
    ("automation.empty_query", "Empty query", "automation", ""),
    ("automation.case", "Case Test", "automation", "Case Test"),
)

CONFIGS = {
    "automation": {
        "id": "1700000000001",
        "alias": "Lights off at night",
        "description": "Turn off living room lights",
        "triggers": [{"trigger": "time", "at": "23:00:00"}],
        "conditions": [],
        "actions": [{
            "action": "light.turn_off",
            "target": {"entity_id": ["light.living_room_lamp", "light.bed_light"]},
        }],
        "mode": "single",
    },
    "script": {
        "alias": "Morning routine",
        "sequence": [
            {"action": "switch.turn_on", "target": {"entity_id": "switch.coffee_maker"}},
            {"delay": {"minutes": 5}},
            {"action": "light.turn_on", "target": {"entity_id": "light.kitchen_ceiling"},
             "data": {"brightness_pct": 60}},
        ],
        "mode": "single",
        "icon": "mdi:weather-sunny",
    },
    "scene": {
        "id": "1700000000002",
        "name": "Movie night",
        "entities": {
            "light.living_room_lamp": {"state": "on", "brightness": 40},
            "media_player.living_room_tv": {"state": "on"},
        },
        "icon": "mdi:movie",
    },
    "helper": {"name": "Vacation mode", "icon": "mdi:airplane", "initial": None},
    "nested": {
        "a": {"b": {"c": [1, 2.5, 3.5, True, False, None, {"deep": "Kitchen sensor"}]}},
        "flag": True,
        "none": None,
    },
    "empty": {},
    "list_top": ["only", "strings", 42],
    "separators_only": {"-": "_."},
    "scalar": "Kitchen",
    "none": None,
}
CONFIG_QUERIES = (
    "light.bed_light", "coffee", "coffe", "turn_off", "living room", "movie",
    "true", "3.5", "single", "brightness", "zzz", "mdi:airplane", "entity_id",
    "23:00", "kitchen", "", "-", "sensor kitchen", "brightnes pct", "night lights",
    "lights", "only strings", "42", "none",
    # Typo fallback at and around its 70 threshold.
    "weathr", "turnoff", "airplan", "lihgts", "brite",
)

# (entity_id, friendly_name, fuzzy_name_score, config_match_score, query_lower)
DEEP_MATCHES = (
    ("automation.lights_off_at_night", "Lights off at night", 120, 0, "lights"),
    ("automation.lights_off_at_night", "Lights off at night", 59, 60, "night"),
    ("automation.lights_off_at_night", "Lights off at night", 60, 0, "nigh"),
    ("automation.lights_off_at_night", "Lights off at night", 79, 85, "xyz"),
    ("script.morning_routine", "Morning Routine", 30, 100, "coffee"),
    ("script.morning_routine", "Morning Routine", 30, 0, "morning routine"),
    ("script.morning_routine", "Morning Routine", 0, 0, "zzz"),
    ("scene.movie_night", "Movie night", 80, 0, "scene.movie"),
    ("scene.movie_night", "Movie night", 0, 0, ""),
)
DEEP_THRESHOLDS = (60, 80)

RATIOS = (
    ("kitchen", "kitchn"), ("", "abc"), ("abc", ""), ("", ""),
    ("bed light", "light bed"), ("light", "light.bed_light"),
    ("temprature", "temperature"), ("living room tv", "tv"),
    ("lumière", "lumiere"), ("a", "a"), ("xyz", "abc"),
    ("kitchen ceiling", "Kitchen Ceiling"), ("ab", "abab"), ("abcd", "abdc"),
    ("night lights", "lights off at night"), ("aaaa", "aaab"),
)

HIDDEN_PENALTIES = (
    (100, None), (100, "user"), (15, "integration"), (20, "user"), (0, None),
    (79.6, None), (79.6, "user"), (50, ""),
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_upstream(root: Path) -> tuple[ModuleType, type]:
    for relative, expected in SOURCE_SHA256.items():
        actual = sha256_file(root / relative)
        if actual != expected:
            raise SystemExit(f"{relative}: sha256 {actual} != pinned {expected}")
    for name in ("ha_mcp", "ha_mcp.utils", "ha_mcp.tools", "ha_mcp.tools.smart_search"):
        if name in sys.modules:
            raise SystemExit(f"{name} is already importable; use an isolated Python")
        placeholder = ModuleType(name)
        placeholder.__path__ = []
        sys.modules[name] = placeholder
    base = ModuleType("ha_mcp.tools.smart_search._base")

    class _SearchBase:
        settings = None

    base._SearchBase = _SearchBase
    sys.modules[base.__name__] = base
    fuzzy = _load(root / FUZZY_PATH, "ha_mcp.utils.fuzzy_search")
    scoring = _load(root / SCORING_PATH, "ha_mcp.tools.smart_search._scoring")
    return fuzzy, scoring.ScoringMixin


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def project(match: dict) -> dict:
    return {key: match[key] for key in ("entity_id", "friendly_name", "domain", "score", "match_type")}


def build(root: Path) -> dict:
    fuzzy, scoring_mixin = load_upstream(root)

    class Scorer(scoring_mixin):
        def __init__(self, fuzzy_threshold: int) -> None:
            self.settings = SimpleNamespace(fuzzy_threshold=fuzzy_threshold)

    entities = [dict(record) for record in ENTITIES]
    entity_search = []
    for threshold in ENTITY_THRESHOLDS:
        searcher = fuzzy.FuzzyEntitySearcher(threshold=threshold)
        for query in ENTITY_QUERIES:
            results, total = searcher.search_entities(entities, query, limit=len(entities) + 1)
            entity_search.append({
                "query": query, "threshold": threshold, "total": total,
                "results": [project(match) for match in results],
            })
    paged = []
    for query, threshold, limit, offset in PAGED:
        searcher = fuzzy.FuzzyEntitySearcher(threshold=threshold)
        results, total = searcher.search_entities(entities, query, limit=limit, offset=offset)
        paged.append({
            "query": query, "threshold": threshold, "limit": limit, "offset": offset,
            "total": total, "results": [project(match) for match in results],
        })
    no_entities = fuzzy.FuzzyEntitySearcher().search_entities([], "light")
    name_searcher = fuzzy.FuzzyEntitySearcher()
    name_scores = [
        {"entity_id": eid, "friendly_name": name, "domain": domain, "query": query,
         "score": name_searcher._calculate_entity_score(eid, name, domain, query)}
        for eid, name, domain, query in NAME_SCORES
    ]
    config_scorer = Scorer(60)
    config_scores = [
        {"config": key, "query": query, "exact_match": exact,
         "score": config_scorer._search_in_dict(CONFIGS[key], query, exact)}
        for key in CONFIGS for query in CONFIG_QUERIES for exact in (True, False)
    ]
    deep_matches = []
    for threshold in DEEP_THRESHOLDS:
        scorer = Scorer(threshold)
        for eid, name, name_score, config_score, query in DEEP_MATCHES:
            for exact in (True, False):
                deep_matches.append({
                    "entity_id": eid, "friendly_name": name, "fuzzy_name_score": name_score,
                    "config_match_score": config_score, "query_lower": query,
                    "exact_match": exact, "fuzzy_threshold": threshold,
                    "result": list(scorer._score_deep_match(
                        eid, name, name_score, config_score, query, exact)),
                })
    ratios = [
        {"query": query, "value": value,
         "ratio": fuzzy.calculate_ratio(query, value),
         "partial_ratio": fuzzy.calculate_partial_ratio(query, value),
         "token_sort_ratio": fuzzy.calculate_token_sort_ratio(query, value)}
        for query, value in RATIOS
    ]
    hidden = [
        {"score": score, "hidden_by": hidden_by,
         "result": fuzzy.apply_hidden_penalty(score, hidden_by)}
        for score, hidden_by in HIDDEN_PENALTIES
    ]
    tokens = sorted({query for query in ENTITY_QUERIES} | {r["entity_id"] for r in ENTITIES})
    constants = {
        "HIDDEN_SCORE_PENALTY": fuzzy.HIDDEN_SCORE_PENALTY,
        "BM25_K1": fuzzy.BM25Scorer().k1,
        "BM25_B": fuzzy.BM25Scorer().b,
        "DEFAULT_FUZZY_THRESHOLD": fuzzy.FuzzyEntitySearcher().threshold,
    }
    return {
        "schema": "hamcp481-scoring-golden-v1",
        "source": {
            "repository": SOURCE_REPOSITORY, "version": SOURCE_VERSION,
            "commit": SOURCE_COMMIT, "sha256": SOURCE_SHA256,
        },
        "constants": constants,
        "entities": entities,
        "entity_search": entity_search,
        "paged": paged,
        "no_entities": list(no_entities),
        "tokenize": [{"text": text, "tokens": fuzzy.tokenize(text)} for text in tokens],
        "name_scores": name_scores,
        "configs": CONFIGS,
        "config_scores": config_scores,
        "deep_matches": deep_matches,
        "ratios": ratios,
        "hidden_penalty": hidden,
    }


def render(document: dict) -> bytes:
    """One JSON value per list item per line: small, stable diffs."""
    lines = ["{"]
    keys = sorted(document)
    for index, key in enumerate(keys):
        value = document[key]
        comma = "," if index < len(keys) - 1 else ""
        if isinstance(value, list):
            lines.append(f"{json.dumps(key)}: [")
            for item_index, item in enumerate(value):
                item_comma = "," if item_index < len(value) - 1 else ""
                lines.append(json.dumps(item, sort_keys=True, ensure_ascii=True) + item_comma)
            lines.append("]" + comma)
        else:
            lines.append(f"{json.dumps(key)}: "
                         f"{json.dumps(value, sort_keys=True, ensure_ascii=True)}{comma}")
    lines.append("}")
    return ("\n".join(lines) + "\n").encode("ascii")


def provenance(golden: bytes) -> bytes:
    record = {
        "schema": "hamcp481-scoring-provenance-v1",
        "source": {
            "repository": SOURCE_REPOSITORY, "version": SOURCE_VERSION,
            "commit": SOURCE_COMMIT, "files": SOURCE_SHA256,
            "executed": [FUZZY_PATH, SCORING_PATH],
            "stubbed": {"ha_mcp.tools.smart_search._base": "_SearchBase with settings only"},
        },
        "generator": {
            "path": "docs/evidence/hamcp481/fixtures/generate_scoring_fixtures.py",
            "sha256": sha256_file(Path(__file__)),
        },
        "golden": {"path": f"docs/evidence/hamcp481/fixtures/{GOLDEN_NAME}",
                   "sha256": hashlib.sha256(golden).hexdigest()},
        "python": "{}.{}.{}".format(*sys.version_info[:3]),
        "isolation": "python -I in a venv with no packages, under unshare -rn (no network)",
        "command": COMMAND,
        "inputs": "synthetic, defined in the generator",
    }
    return (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    golden = render(build(args.upstream))
    outputs = {GOLDEN_NAME: golden, PROVENANCE_NAME: provenance(golden)}
    if args.check:
        stale = [name for name, data in outputs.items()
                 if (args.output_dir / name).read_bytes() != data]
        if stale:
            print("stale: " + ", ".join(stale), file=sys.stderr)
            return 1
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        (args.output_dir / name).write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
