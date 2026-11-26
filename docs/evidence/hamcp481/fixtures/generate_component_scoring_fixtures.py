"""HAMCP-501 W6b: golden scores from the pinned ``ha_mcp_tools`` component scorers.

Live ha-mcp 9.2.0 answers ``ha_search`` on the custom component's
in-process route, so its scorers are the parity basis for native search
(``search/scoring.py`` and the area ladder in ``search/entities.py``).

Reads four upstream files by path, after checking their SHA-256, and runs
only what the scorers need:

- ``websocket_api/search_score.py`` runs whole, as
  ``ha_mcp_tools.websocket_api.search_score``. Its one relative import,
  ``.constants``, resolves to a stand-in that holds only the three
  constants it imports.
- ``websocket_api/constants.py`` is never imported (it imports Home
  Assistant). The top-level assignments of ``FUZZY_THRESHOLD``,
  ``HIDDEN_SCORE_PENALTY`` and ``_SPLIT_RE`` are taken from its syntax tree,
  each checked to be a literal or ``re.compile`` of a literal, and
  evaluated with ``re`` alone.
- ``websocket_api/search.py`` is never imported (it imports Home
  Assistant). ``_entity_match_type``, ``_is_alias_driven`` and
  ``_get_match_type_tier`` are taken from its syntax tree and run against
  ``search_score._tokenize`` and the extracted ``_SPLIT_RE``; any other free
  name fails the run.
- ``search_locations.py`` imports only the standard library and runs whole.

Package parents are empty placeholder modules, so no other upstream file can
be imported. Nothing is edited, nothing contacts a network or Home
Assistant, and every input is the synthetic corpus below.

The output records the inputs and the upstream outputs side by side, and
``tests/test_hamcp481_scoring_parity.py`` checks the Engineering port against
it. The W1 generator, ``generate_scoring_fixtures.py``, and its outputs stay
as history: they pin the legacy server scorers W6b retired.

Usage (from a Python with no packages installed, outside the upstream tree,
in a network namespace with no network; a source tree of ha-mcp at commit
51e3aac7 holds the files)::

    unshare -rn <venv>/bin/python -I docs/evidence/hamcp481/fixtures/generate_component_scoring_fixtures.py \
        --upstream <ha-mcp-51e3aac7> \
        --output-dir docs/evidence/hamcp481/fixtures

``--check`` regenerates in memory and fails when a committed file differs.
"""

from __future__ import annotations

import __future__ as future
import argparse
import ast
import builtins
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType

SOURCE_REPOSITORY = "https://github.com/homeassistant-ai/ha-mcp"
SOURCE_VERSION = "9.2.0"
SOURCE_COMMIT = "51e3aac788482a081807591cd5d58d07dc4e540b"
COMPONENT = "custom_components/ha_mcp_tools"
SCORE_PATH = f"{COMPONENT}/websocket_api/search_score.py"
SEARCH_PATH = f"{COMPONENT}/websocket_api/search.py"
CONSTANTS_PATH = f"{COMPONENT}/websocket_api/constants.py"
LOCATIONS_PATH = f"{COMPONENT}/search_locations.py"
SOURCE_SHA256 = {
    SCORE_PATH: "ba034030b149091fba1e2777ac6c0c4d9c442fa33148b7d92441cba3feca43de",
    SEARCH_PATH: "5f1a1e312884494245f71c4720c5ddd3ac3d4211f9c8257e9be9460be36781a8",
    CONSTANTS_PATH: "672c13bb636c77c549cb27a72cf27c304d8ac1a61cc833348e4531ad40cbfa71",
    LOCATIONS_PATH: "4567798ee26475efd88e9ed14f9261262e84132d4f453bd6f32789c038c30fd3",
}
EXTRACTED_CONSTANTS = ("FUZZY_THRESHOLD", "HIDDEN_SCORE_PENALTY", "_SPLIT_RE")
EXTRACTED_FUNCTIONS = ("_entity_match_type", "_is_alias_driven", "_get_match_type_tier")
# The modules each executed file may import; anything else fails the run.
ALLOWED_IMPORTS = {
    SCORE_PATH: {("__future__", 0), ("difflib", 0), ("typing", 0), ("constants", 1)},
    LOCATIONS_PATH: {("__future__", 0), ("collections.abc", 0), ("dataclasses", 0),
                     ("difflib", 0), ("typing", 0)},
}
GOLDEN_NAME = "component-scoring-golden.json"
PROVENANCE_NAME = "component-scoring-provenance.json"
COMMAND = (
    "unshare -rn <venv>/bin/python -I"
    " docs/evidence/hamcp481/fixtures/generate_component_scoring_fixtures.py"
    " --upstream <ha-mcp-51e3aac7> --output-dir docs/evidence/hamcp481/fixtures"
)

# Match-text lists shaped like ``_entity_record``'s ``_match_texts``
# (search.py 548-564): the entity id, the composed name, the friendly name,
# the aliases, the label names, the device texts, the area and the floor.
# Falsy and non-string texts are included on purpose.
TEXTS = {
    "kitchen_ceiling": ["light.kitchen_ceiling", "Kitchen Ceiling Light", "Kitchen Ceiling",
                        "Cooking lamp", "Night", "Hue Bridge", "Signify", "LCT015", "Kitchen",
                        "Main Floor"],
    "bathroom_leak": ["binary_sensor.leak_bathroom", "Bathroom Leak", "Bathroom Leak", "Water",
                      "Acme", "Bathroom", "Upstairs"],
    "uptime": ["sensor.uptime", "", "", None],
    "vacation": ["input_boolean.vacation_mode", "Vacation Mode", "Vacation Mode"],
    "lumiere": ["light.salon_lumiere", "Lumière Salon", "Lumière Salon"],
    "coffee": ["switch.coffee_maker", "Coffee Maker", "Coffee Maker", "Espresso", 7],
    "separators": ["sensor.a_b_c", "A-B  C", "A.B_C"],
    "ratio_70": ["abcdefgxyz"],
    "ratio_69": ["abcdefghixyzw"],
    # A substring hit beside a higher ratio: the substring tier wins.
    "substring_and_ratio": ["xxabcdxx", "abcxd"],
    # Long enough for SequenceMatcher's autojunk heuristic, which is off.
    "long_repeat": ["ab" * 50 + "x" + "ab" * 50],
    "empty": [],
}
TEXT_QUERIES = (
    "light", "kitchen", "kitchen ceiling", "light.kitchen_ceiling", "kitchen_ceiling",
    "kitchen-ceiling", "kitchen  ceiling", "kitchn", "kitchen celing", "ceiling kitchen",
    "cooking", "signify", "lct015", "main floor", "night", "leak", "bathroom leak",
    "water", "upstairs", "xyzzy", "", "vacation", "vacation mode", "vacation_mode",
    "input boolean", "lumière", "lumiere", "espresso", "7", "coffe maker", "a b c",
    "a.b.c", "abc", "binary sensor", "sensor", "uptime", "a",
    # Whole-string ratios exactly at 70 and at 69 against the ratio texts.
    "abcdefghij", "abcdefghiklmn",
    # A substring of one text and a ratio of 88 against another.
    "abcd",
    # A ratio of 99 without autojunk and 49 with it.
    "ab" * 100,
)

# Configuration name pairs, as ``_config_score`` passes ``[entity_id, name]``.
NAMES = (
    ("automation.lights_off_at_night", "Lights off at night"),
    ("script.morning_routine", "Morning Routine"),
    ("input_boolean.vacation_mode", "Vacation Mode"),
    ("input_boolean.vacation_mode", "vacation_mode"),
    ("scene.movie_night", "Movie night"),
    ("automation.x", ""),
    ("automation.salon_lumiere", "Salon lumière"),
    # Whole-string ratios exactly at 70 and at 69, and a null name.
    ("sensor.ratio_seventy", "abcdefgxyz"),
    ("sensor.ratio_sixty_nine", "abcdefghixyzw"),
    ("automation.unnamed", None),
)
NAME_QUERIES = (
    "lights", "lights off at night", "lights_off", "lights-off-at-night", "night lights",
    "nite", "morning", "morning_routine", "morning routine", "routine morning",
    "vacation mode", "input boolean", "input_boolean", "movie", "x", "", "xyzzy",
    "lumiere", "salon lumière", "mornin rutine", "automation", "scene movie",
    "abcdefghij", "abcdefghiklmn", "none",
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
    "select": {"name": "House mode", "options": ["Home", "Away", "Night"], "icon": None},
    "nested": {
        "a": {"b": {"c": [1, 2.5, 3.5, True, False, None, {"deep": "Kitchen sensor"}]}},
        "flag": True,
        "none": None,
    },
    "empty": {},
    "list_top": ["only", "strings", 42],
    "separators_only": {"-": "_."},
    # A mixed-case key with a null leaf: only the lowered key matches.
    "mixed_case_key": {"KeyOnly": None},
}
CONFIG_QUERIES = (
    "light.bed_light", "coffee", "coffe", "turn_off", "living room", "movie", "true",
    "3.5", "single", "brightness", "zzz", "mdi:airplane", "entity_id", "23:00", "kitchen",
    "", "-", "sensor kitchen", "brightnes pct", "night lights", "lights", "only strings",
    "42", "none", "away", "awya", "weathr", "turnoff", "airplan", "lihgts", "brite",
    "keyonly",
)
# (entity_id, name, config key or None) for ``_config_score``.
CONFIG_ITEMS = (
    ("automation.lights_off_at_night", "Lights off at night", "automation"),
    ("script.morning_routine", "Morning Routine", "script"),
    ("scene.movie_night", "Movie night", "scene"),
    ("input_boolean.vacation_mode", "Vacation mode", "helper"),
    ("input_select.house_mode", "House mode", "select"),
    ("input_boolean.empty", "Empty", "empty"),
    ("automation.no_body", "No body", None),
)
CONFIG_ITEM_QUERIES = (
    "lights", "night", "nite", "light.bed_light", "coffee", "coffe", "morning routine",
    "movie", "vacation", "airplan", "away", "empty", "body", "xyzzy", "routine_morning",
    "input boolean",
    # In the entity id only, not in the name or the body.
    "lights_off",
)

# (query_lower, entity_id, names, domain, aliases)
MATCH_TYPES = (
    ("light.kitchen_ceiling", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"),
     "light", ["Cooking lamp"]),
    ("kitchen ceiling", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"),
     "light", ["Cooking lamp"]),
    ("kitchen ceiling light", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"),
     "light", []),
    ("light", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"), "light", []),
    ("kitchen_ceil", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"),
     "light", []),
    ("ceiling light", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"),
     "light", []),
    ("cooking", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"),
     "light", ["Cooking lamp"]),
    ("cookinglamp", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"),
     "light", ["Cooking lamp"]),
    ("kitchen lamp", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"),
     "light", ["Kitchen lamp"]),
    ("kitchen", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"),
     "light", ["Kitchen"]),
    ("signify", "light.kitchen_ceiling", ("Kitchen Ceiling Light", "Kitchen Ceiling"),
     "light", []),
    ("leak", "binary_sensor.leak_bathroom", ("Bathroom Leak", "Bathroom Leak"),
     "binary_sensor", []),
    ("binary_sensor", "binary_sensor.leak_bathroom", ("Bathroom Leak", "Bathroom Leak"),
     "binary_sensor", []),
    ("bathroom leak", "binary_sensor.leak_bathroom", ("Bathroom Leak", "Bathroom Leak"),
     "binary_sensor", []),
    ("espresso", "switch.coffee_maker", ("Coffee Maker", "Coffee Maker"), "switch",
     ["Espresso", "Coffee"]),
    ("coffee", "switch.coffee_maker", ("Coffee Maker", "Coffee Maker"), "switch",
     ["Espresso", "Coffee"]),
    ("uptime", "sensor.uptime", ("sensor.uptime", "sensor.uptime"), "sensor", []),
    ("vacation", "input_boolean.vacation_mode", ("Vacation Mode", "Vacation Mode"),
     "input_boolean", ["Holiday", "vacation-mode"]),
    ("holiday mode", "input_boolean.vacation_mode", ("Vacation Mode", "Vacation Mode"),
     "input_boolean", ["Holiday"]),
    ("vacationmode", "input_boolean.vacation_mode", ("Vacation Mode", "Vacation Mode"),
     "input_boolean", ["vacation-mode"]),
    ("lumière", "light.salon_lumiere", ("Lumière Salon", "Lumière Salon"), "light", []),
    ("nomatch", "light.salon_lumiere", ("Lumière Salon", "Lumière Salon"), "light", []),
    # An alias token or concat that the id or a name already holds is not
    # alias-only: the id tokens, the id tail concat, the name tokens and
    # the name concat.
    ("desk", "light.desk_lamp", ("Other",), "light", ["Desk"]),
    ("desklamp", "light.desk_lamp", ("Other",), "light", ["Desk Lamp"]),
    ("lamp", "light.x1", ("Desk Lamp",), "light", ["Lamp"]),
    ("desklamp", "light.x1", ("Desk Lamp",), "light", ["Desk-Lamp"]),
)

# Area and floor registries, as ``_resolve_area_query`` reads them.
AREAS = {
    "kitchen": {"name": "Kitchen", "aliases": ["Cookhouse"], "floor_id": "main"},
    "living_room": {"name": "Living Room", "aliases": [], "floor_id": "main"},
    "garden": {"name": "Garden", "aliases": ["Yard"], "floor_id": None},
    "nursery": {"name": "Nursery", "aliases": [], "floor_id": "upstairs"},
    "bathroom": {"name": "Bathroom", "aliases": ["Restroom"], "floor_id": "upstairs"},
    "attic": {"name": "Attic", "aliases": [], "floor_id": "attic_floor"},
    "basement": {"name": "Basement", "aliases": [], "floor_id": "basement_floor"},
}
FLOORS = {
    "main": {"name": "Main Floor", "aliases": ["Downstairs"]},
    "upstairs": {"name": "Upstairs", "aliases": ["First Floor"]},
    "attic_floor": {"name": "Attic Level", "aliases": []},
    "basement_floor": {"name": "Basement Level", "aliases": []},
}
AREA_QUERIES = (
    "kitchen", "Kitchen", "  kitchen  ", "cookhouse", "living_room", "living room",
    "main floor", "downstairs", "upstairs", "first floor", "main", "uppstairs",
    "downstair", "kitchn", "livng room", "bath", "restrom", "yard", "garden", "xyzzy",
    "attic", "basement level", "basemnt level", "level", "attic levl", "floor",
)
AREA_FLOOR_SETS = {
    "full": (AREAS, FLOORS),
    # Two fuzzy-equal floors: the ambiguous-floor branch.
    "twin_floors": (
        {"den": {"name": "Den", "aliases": [], "floor_id": "east"},
         "study": {"name": "Study", "aliases": [], "floor_id": "west"}},
        {"east": {"name": "Wing A", "aliases": []}, "west": {"name": "Wing B", "aliases": []}},
    ),
    "no_floors": (AREAS, {}),
    # A partial ratio of exactly 80 on an area, of 79 on another, and a
    # whole-string ratio of exactly 80 on a floor: the threshold boundaries.
    "area_80": ({"t80": {"name": "Abcde", "aliases": [], "floor_id": None}}, {}),
    "area_79": ({"t79": {"name": "Abcdefghijklmnopqrstuvwx", "aliases": [], "floor_id": None}}, {}),
    "floor_80": ({"room": {"name": "Room", "aliases": [], "floor_id": "f80"}},
                 {"f80": {"name": "Vwxyz", "aliases": []}}),
    # An area and a floor with the same name: the exact area wins.
    "collision": ({"loft": {"name": "Loft", "aliases": [], "floor_id": "top"},
                   "den": {"name": "Den", "aliases": [], "floor_id": "top"}},
                  {"top": {"name": "Loft", "aliases": ["Top"]}}),
}
AREA_SET_QUERIES = {
    "twin_floors": ("wing", "wing c", "wing a", "den", "study", "stud"),
    "area_80": ("abcdx", "abcde"),
    "area_79": ("abcdefghijklmnopqrsyyyyy",),
    "floor_80": ("vwxyq", "vwxyz"),
    "collision": ("loft", "top", "lofty"),
}

RATIOS = (
    ("kitchen", "kitchn"), ("", "abc"), ("abc", ""), ("", ""), ("bed light", "light bed"),
    ("light", "light.bed_light"), ("temprature", "temperature"), ("living room tv", "tv"),
    ("lumière", "lumiere"), ("a", "a"), ("xyz", "abc"), ("kitchen ceiling", "Kitchen Ceiling"),
    ("ab", "abab"), ("abcd", "abdc"), ("night lights", "lights off at night"),
    ("aaaa", "aaab"), ("abcdefghij", "abcdefgxyz"), ("abcdefghiklmn", "abcdefghixyzw"),
    ("bath", "bathroom"), ("restrom", "restroom"), ("kitchn", "kitchen"), ("tv", "living room tv"),
)
TOKENIZE = (
    "light.kitchen_ceiling", "Kitchen  Ceiling", "a-b_c.d e", "", "  ", "_._", "Lumière Salon",
    "input_boolean", "binary_sensor.leak_bathroom", "A--B__C", "tab\there", "x", "LCT015",
)
HIDDEN_PENALTIES = (
    (100, False), (100, True), (80, True), (15, True), (20, True), (0, False), (79.6, False),
    (79.6, True), (70, True),
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(root: Path, relative: str) -> ast.Module:
    return ast.parse((root / relative).read_bytes(), filename=relative)


def _check_imports(tree: ast.Module, relative: str) -> None:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update((alias.name, 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add((node.module, node.level))
    if not found <= ALLOWED_IMPORTS[relative]:
        raise SystemExit(f"{relative}: unexpected imports {sorted(found - ALLOWED_IMPORTS[relative])}")


def _literal_or_compile(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return True
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "re"
            and node.func.attr == "compile" and not node.keywords
            and all(isinstance(arg, ast.Constant) for arg in node.args))


def extract_constants(root: Path) -> dict:
    assignments: dict[str, list[ast.Assign]] = {name: [] for name in EXTRACTED_CONSTANTS}
    for node in _tree(root, CONSTANTS_PATH).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in assignments:
                assignments[target.id].append(node)
    values: dict = {}
    for name, nodes in assignments.items():
        if len(nodes) != 1 or not _literal_or_compile(nodes[0].value):
            raise SystemExit(f"{CONSTANTS_PATH}: {name} is not one literal assignment")
        code = compile(ast.Module(body=nodes, type_ignores=[]), CONSTANTS_PATH, "exec",
                       dont_inherit=True)
        namespace = {"__builtins__": {}, "re": re}
        exec(code, namespace)
        values[name] = namespace[name]
    return values


def extract_functions(root: Path, namespace: dict) -> dict:
    found: dict[str, list[ast.FunctionDef]] = {name: [] for name in EXTRACTED_FUNCTIONS}
    for node in _tree(root, SEARCH_PATH).body:
        if isinstance(node, ast.FunctionDef) and node.name in found:
            found[node.name].append(node)
    nodes = []
    for name, defs in found.items():
        if len(defs) != 1 or defs[0].decorator_list:
            raise SystemExit(f"{SEARCH_PATH}: {name} is not one plain top-level function")
        nodes.append(defs[0])
    module = ast.Module(body=nodes, type_ignores=[])
    allowed = set(namespace) | set(EXTRACTED_FUNCTIONS) | set(dir(builtins))
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef):
            bound = {arg.arg for arg in (*node.args.posonlyargs, *node.args.args,
                                         *node.args.kwonlyargs)}
            allowed |= bound
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            allowed.add(node.id)
    loads = {node.id for node in ast.walk(module)
             if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    if not loads <= allowed:
        raise SystemExit(f"{SEARCH_PATH}: unexpected free names {sorted(loads - allowed)}")
    # Annotations stay unevaluated, as under the source's own
    # ``from __future__ import annotations``.
    code = compile(module, SEARCH_PATH, "exec", flags=future.annotations.compiler_flag,
                   dont_inherit=True)
    scope = dict(namespace)
    exec(code, scope)
    return {name: scope[name] for name in EXTRACTED_FUNCTIONS}


def load_upstream(root: Path) -> tuple[dict, ModuleType, dict, ModuleType]:
    for relative, expected in SOURCE_SHA256.items():
        actual = sha256_file(root / relative)
        if actual != expected:
            raise SystemExit(f"{relative}: sha256 {actual} != pinned {expected}")
    for relative in ALLOWED_IMPORTS:
        _check_imports(_tree(root, relative), relative)
    if any(name == "ha_mcp_tools" or name.startswith("ha_mcp_tools.") for name in sys.modules) \
            or importlib.util.find_spec("ha_mcp_tools") is not None:
        raise SystemExit("ha_mcp_tools is already importable; use an isolated Python")
    for name in ("ha_mcp_tools", "ha_mcp_tools.websocket_api"):
        placeholder = ModuleType(name)
        placeholder.__path__ = []
        sys.modules[name] = placeholder
    constants = extract_constants(root)
    stand_in = ModuleType("ha_mcp_tools.websocket_api.constants")
    stand_in.__dict__.update(constants)
    sys.modules[stand_in.__name__] = stand_in
    score = _load(root / SCORE_PATH, "ha_mcp_tools.websocket_api.search_score")
    match = extract_functions(root, {"_tokenize": score._tokenize,
                                     "_SPLIT_RE": constants["_SPLIT_RE"]})
    locations = _load(root / LOCATIONS_PATH, "ha_mcp_tools.search_locations")
    return constants, score, match, locations


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def build(root: Path) -> dict:
    constants, score, match, locations = load_upstream(root)
    text_tiers = [
        {"query_lower": query, "texts": key, "fuzzy": fuzzy,
         "tier": score._text_tier(query, TEXTS[key], fuzzy=fuzzy)}
        for key in TEXTS for query in TEXT_QUERIES for fuzzy in (False, True)
    ]
    name_tiers = [
        {"query_lower": query, "texts": list(names), "exact": exact,
         "tier": score._name_tier(query, list(names), exact=exact)}
        for names in NAMES for query in NAME_QUERIES for exact in (True, False)
    ]
    body_scores = [
        {"config": key, "query_lower": query, "exact": exact,
         "score": score._config_body_score(query, CONFIGS[key], exact=exact)}
        for key in CONFIGS for query in CONFIG_QUERIES for exact in (True, False)
    ]
    config_scores = []
    for entity_id, name, key in CONFIG_ITEMS:
        body = CONFIGS[key] if key is not None else None
        for query in CONFIG_ITEM_QUERIES:
            for exact in (True, False):
                result = score._config_score(query, entity_id, name, body, exact=exact)
                config_scores.append({
                    "entity_id": entity_id, "name": name, "config": key, "query_lower": query,
                    "exact": exact, "result": None if result is None else list(result),
                })
    match_types = [
        {"query_lower": query, "entity_id": entity_id, "names": list(names), "domain": domain,
         "aliases": aliases, "exact": exact,
         "match_type": match["_entity_match_type"](query, entity_id, names, domain, aliases,
                                                    exact=exact),
         "alias_driven": match["_is_alias_driven"](query, entity_id, names, aliases)}
        for query, entity_id, names, domain, aliases in MATCH_TYPES for exact in (True, False)
    ]
    area_queries = []
    for set_name, (areas, floors) in AREA_FLOOR_SETS.items():
        for query in AREA_SET_QUERIES.get(set_name, AREA_QUERIES):
            for available in ((True, False) if set_name in ("full", "collision") else (True,)):
                area_ids, warnings = locations._resolve_area_query(
                    areas, floors, query, floor_registry_available=available)
                area_queries.append({
                    "set": set_name, "query": query, "floor_registry_available": available,
                    "area_ids": sorted(area_ids), "warnings": warnings,
                })
    ratios = [
        {"a": a, "b": b, "ratio": score._calc_ratio(a, b),
         "area_ratio": locations._ratio(a, b), "partial_ratio": locations._partial_ratio(a, b)}
        for a, b in RATIOS
    ]
    tokens = [{"text": text, "tokens": score._tokenize(text),
               "sep_normalized": score._sep_normalized(text)} for text in TOKENIZE]
    hidden = [{"score": value, "hidden": flag, "result": score._apply_hidden_penalty(value, flag)}
              for value, flag in HIDDEN_PENALTIES]
    return {
        "schema": "hamcp501-component-scoring-golden-v1",
        "source": {
            "repository": SOURCE_REPOSITORY, "version": SOURCE_VERSION,
            "commit": SOURCE_COMMIT, "sha256": SOURCE_SHA256,
        },
        "constants": {
            "FUZZY_THRESHOLD": constants["FUZZY_THRESHOLD"],
            "HIDDEN_SCORE_PENALTY": constants["HIDDEN_SCORE_PENALTY"],
            "_SPLIT_RE": constants["_SPLIT_RE"].pattern,
        },
        "texts": TEXTS,
        "text_tiers": text_tiers,
        "name_tiers": name_tiers,
        "configs": CONFIGS,
        "body_scores": body_scores,
        "config_scores": config_scores,
        "match_types": match_types,
        "area_sets": {name: {"areas": areas, "floors": floors}
                      for name, (areas, floors) in AREA_FLOOR_SETS.items()},
        "area_queries": area_queries,
        "ratios": ratios,
        "tokenize": tokens,
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
        "schema": "hamcp501-component-scoring-provenance-v1",
        "source": {
            "repository": SOURCE_REPOSITORY, "version": SOURCE_VERSION,
            "commit": SOURCE_COMMIT, "files": SOURCE_SHA256,
            "executed": [SCORE_PATH, LOCATIONS_PATH],
            "extracted": {
                CONSTANTS_PATH: list(EXTRACTED_CONSTANTS),
                SEARCH_PATH: list(EXTRACTED_FUNCTIONS),
            },
            "stubbed": {
                "ha_mcp_tools.websocket_api.constants":
                    "the three extracted constants only",
            },
        },
        "generator": {
            "path": "docs/evidence/hamcp481/fixtures/generate_component_scoring_fixtures.py",
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
