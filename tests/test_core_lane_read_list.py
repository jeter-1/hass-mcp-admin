"""Offline D5 coverage of the seven lane groups' Core dependencies by the O3 read list (HAMCP-443 D5).

O3 admits a release to the lanes only when every path on ci/core-contract-read-files.json is
byte-identical to the reviewed anchor's, so a Core file a lane depends on that is not on that list
can drift and still reach the lane. Each such file is a disclosed GAP below, with its disposition;
the PR body carries the same list. The dependencies are derived from the lane source, never
declared: each harness's configuration, and the REST endpoints, config-flow handlers and WebSocket
commands its reachable script code sends. A command the Engineering runtime or the capability's
reviewed upstream provider owns is the capability's own, and its Core files are the capability's
profile, which is on the list. Anything
the derivation cannot map fails. The twelve run_specialized groups are outside this test.
"""
import ast
import json
from pathlib import Path
import re
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hass_mcp_engineering_beta"), str(ROOT / "scripts"), str(ROOT / "tests")]

import core_evidence_lane_group as lg  # noqa: E402
import core_lane_host  # noqa: E402
from core_lane_bindings import REVIEWED_CORE_BINDINGS  # noqa: E402
from core_release_evidence import LANE_GROUPS  # noqa: E402

SCRIPTS = ROOT / "scripts"
READ_LIST = json.loads((ROOT / "ci/core-contract-read-files.json").read_text())
LISTED = frozenset(READ_LIST["paths"])
CAPABILITIES = json.loads((ROOT / "ci/core-evidence-groups.json").read_text())
C = "homeassistant/components/"
ENGINEERING = "ha_mcp_engineering:"
CORE_CONFIG = "homeassistant/core_config.py"
YAML_LOADER = "homeassistant/util/yaml/loader.py"
CONSTRAINTS = "homeassistant/package_constraints.txt"
DUMPS = "tests/components/matter/fixtures/nodes/"

# REST route prefix -> the Core files that serve it ("/config/..." routes are under /api). A
# prefix matches itself or a longer route at a "/" or "?"; EXACT_ROUTES match only whole.
ROUTES = {
    "/api": (C + "api/__init__.py",),
    "/api/config": (C + "api/__init__.py",),
    "/api/config/config_entries": (C + "config/config_entries.py",),
    "/api/onboarding": (C + "onboarding/__init__.py", C + "onboarding/views.py", C + "onboarding/manifest.json"),
    "/api/websocket": (C + "websocket_api/http.py",),
    "/auth/token": (C + "auth/__init__.py",),
    "/config/automation/config": (C + "config/automation.py", C + "config/view.py"),
    "/config/scene/config": (C + "config/scene.py", C + "config/view.py"),
    "/config/script/config": (C + "config/script.py", C + "config/view.py"),
}
EXACT_ROUTES = frozenset({"/api", "/api/config"})
COLLECTION = "homeassistant/helpers/collection.py"
# Each WebSocket command the lane scripts send, exactly, -> the Core files that register it.
COMMANDS = {command: files for files, commands in (
    ((C + "websocket_api/auth.py",), ("auth",)),
    ((C + "websocket_api/commands.py",), ("call_service",)),
    ((C + "config/config_entries.py",), ("config_entries/disable", "config_entries/get")),
    ((C + "config/area_registry.py",), ("config/area_registry/create", "config/area_registry/delete",
                                        "config/area_registry/list")),
    ((C + "config/auth.py",), ("config/auth/list",)),
    ((C + "config/category_registry.py",), ("config/category_registry/create", "config/category_registry/delete",
                                            "config/category_registry/list")),
    ((C + "config/device_registry.py",), ("config/device_registry/list",)),
    ((C + "config/entity_registry.py",), ("config/entity_registry/get", "config/entity_registry/list",
                                          "config/entity_registry/remove", "config/entity_registry/update")),
    ((C + "config/label_registry.py",), ("config/label_registry/create", "config/label_registry/delete",
                                         "config/label_registry/list")),
    ((C + "http/websocket_api.py",), ("http/config",)),
    ((C + "assist_pipeline/pipeline.py", COLLECTION), ("assist_pipeline/pipeline/list",)),
    ((C + "input_boolean/__init__.py", COLLECTION), ("input_boolean/create", "input_boolean/delete",
                                                     "input_boolean/list")),
    ((C + "input_number/__init__.py", COLLECTION), ("input_number/delete", "input_number/list")),
    ((C + "marketplace/websocket/__init__.py",), ("marketplace/info", "marketplace/warning/accept")),
    ((C + "marketplace/websocket/repository.py",), ("marketplace/repository/install",)),
    ((C + "thread/websocket_api.py",), ("thread/add_dataset_tlv",)),
) for command in commands}
# Exact admissions of the "type" expressions the resolver cannot read, one pair each.
DYNAMIC = {
    ("created_object_contract_acceptance", "domain + '/list'"): ("input_boolean/list", "input_number/list"),
    ("created_object_contract_acceptance", "domain + '/delete'"): ("input_boolean/delete", "input_number/delete"),
    # A diagnostics record's own field, not a command.
    ("lane_failure_diagnostics", "kind if kind in SAFE_TYPES else 'Exception'"): (),
}
# Version-specific data keyed to the anchor (D5), with the Core file each value was reviewed from.
ANCHORED = {("thread_matter_topology_a2_acceptance", "NODE_DEVICE_IDENTIFIER"): (C + "matter/helpers.py",)}
CUSTOM = frozenset(path.name for path in (core_lane_host.DEVICE_LANE / "custom_components").iterdir()
                   if path.is_dir())
NODE_IDS = json.loads((core_lane_host.MATTER_STUB / "provenance.json").read_text())["node_dumps"]["node_ids"]

ALL = frozenset(LANE_GROUPS)
STUB = frozenset({"device_configuration_entity", "thread_matter_topology"})
CREATED = frozenset({"scene_create_delete", "created_helper_delete"})
DEFAULT_CONFIG = ("Integration closure only; no lane command goes to it. Drift changes what loads beside the lane, "
                  "not the contract files the proof reads (its profile, on the list); a dropped integration the "
                  "lane uses fails its setup or read, so the group is WITHHELD.")
ONBOARDING = ("Creates the disposable admin before any proof step; no receipt field comes from it. Drift either "
              "still yields the admin token or fails the bootstrap, so the group is WITHHELD.")
GAPS = {
    C + "default_config/__init__.py": (ALL, DEFAULT_CONFIG),
    C + "default_config/manifest.json": (ALL, DEFAULT_CONFIG),
    C + "onboarding/__init__.py": (ALL, ONBOARDING),
    C + "onboarding/manifest.json": (ALL, ONBOARDING),
    C + "onboarding/views.py": (ALL, ONBOARDING),
    C + "matter/config_flow.py": (STUB, "Creates the lane's Matter entry against the stub. Drift either still "
                                        "creates the entry, which the capability then reads through the listed "
                                        "matter files, or fails the entry setup, so the group is WITHHELD."),
    YAML_LOADER: (CREATED, "Parses the lane's !include lines (empty automations, scripts and scenes files). Drift "
                           "either loads them or fails Core's configuration load, so the group is WITHHELD; the "
                           "created objects are proven by read-back through listed files."),
    CONSTRAINTS: (STUB, "Pins the release image's aiohttp, on which the hash-pinned Matter stub runs. The stub "
                        "serves JSON frames over aiohttp's WebSocket server: a library drift that breaks it fails "
                        "the lane's Matter entry setup, and A2 also checks the stub's own command record. Core's "
                        "Matter client is pinned by matter/manifest.json, which is listed."),
    **{DUMPS + name: (STUB, "Outside homeassistant/, so O3 can never list it. Device CI mode reads it from the "
                            "release archive and refuses unless it equals its committed anchor copy (stub_assets); "
                            "A2 serves the committed, hash-pinned anchor copy (stub.py PINNED_DUMPS). Drift "
                            "refuses or never reaches the lane.") for name in NODE_IDS},
}


def route(value, table, exact=frozenset(), separators="/?"):
    """The files of the longest entry matching value; an unmapped value fails."""
    matches = [entry for entry in table if value == entry or (
        entry not in exact and value.startswith(entry) and value[len(entry)] in separators)]
    if not matches:
        raise LookupError("unmapped: " + value)
    return table[max(matches, key=len)]


class Lanes:
    """The lane scripts as ASTs; ``text`` replaces a module's source in a negative test."""

    def __init__(self, text=None):
        self.text, self.trees, self.tables = text or {}, {}, {}

    def tree(self, module):
        if module not in self.trees:
            path = SCRIPTS / (module + ".py")
            source = self.text.get(module) or (path.read_text() if path.is_file() else None)
            self.trees[module] = None if source is None else ast.parse(source)
        return self.trees[module]

    def table(self, module):
        """Top-level definitions; script names and modules imported in any scope; Engineering names."""
        if module not in self.tables:
            defs, names, aliases, engineering = {}, {}, {}, {}
            tree = self.tree(module)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defs[node.name] = node
                elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                        for n in ast.walk(target):
                            if isinstance(n, ast.Name):
                                defs.setdefault(n.id, node)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    for alias in node.names:
                        if self.tree(node.module):
                            names[alias.asname or alias.name] = (node.module, alias.name)
                        elif node.module.split(".")[0] == "ha_mcp_engineering":
                            engineering[alias.asname or alias.name] = node.module + "." + alias.name
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if self.tree(alias.name):
                            aliases[alias.asname or alias.name] = alias.name
            self.tables[module] = defs, names, aliases, engineering
        return self.tables[module]

    def reached(self, root):
        """(module, name, node) of every script definition reachable from the harness's top level."""
        seen, todo, found = set(), [(root, name) for name in self.table(root)[0]], []
        while todo:
            key = todo.pop()
            if key in seen:
                continue
            seen.add(key)
            module, name = key
            defs, names, aliases, _ = self.table(module)
            if name not in defs:
                if name in names:  # A re-exported import.
                    todo.append(names[name])
                continue
            node = defs[name]
            found.append((module, name, node))
            for n in ast.walk(node):
                if isinstance(n, ast.Name):
                    if n.id in defs:
                        todo.append((module, n.id))
                    elif n.id in names:
                        todo.append(names[n.id])
                elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in aliases:
                    todo.append((aliases[n.value.id], n.attr))
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module and self.tree(n.module):
                    todo.extend((n.module, alias.name) for alias in n.names)
        return found

    def strings(self, module, expr):
        """The str values an expression is: a literal, a script constant or a literal tuple's element;
        an Engineering-owned name gives its ENGINEERING marker. Anything else fails."""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return (expr.value,)
        if isinstance(expr, ast.Tuple):
            return tuple(value for element in expr.elts for value in self.strings(module, element))
        if isinstance(expr, ast.Subscript) and isinstance(expr.slice, ast.Constant) and type(expr.slice.value) is int:
            return (self.strings(module, expr.value)[expr.slice.value],)
        if isinstance(expr, ast.Name):
            defs, names, _, engineering = self.table(module)
            node = defs.get(expr.id)
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                return self.strings(module, node.value)
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    return self.strings(module, node.value)
                if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    for name, value in zip(target.elts, node.value.elts, strict=True):
                        if isinstance(name, ast.Name) and name.id == expr.id:
                            return self.strings(module, value)
            if expr.id in names:
                return self.strings(names[expr.id][0], ast.Name(names[expr.id][1]))
            if expr.id in engineering:
                return (ENGINEERING + engineering[expr.id],)
        raise LookupError(module + ": unresolved " + ast.unparse(expr))

    def configured(self, harness):
        """The CONFIGS key of the one configuration the harness writes for its Core."""
        keys = set()
        for module, _, node in self.reached(harness):
            if module != harness:
                continue
            for n in ast.walk(node):
                if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
                    continue
                if n.func.attr == "configuration" and len(n.args) >= 3:
                    keys.add(self.config_key(module, n.args[2]))
                elif (n.func.attr == "write_text" and isinstance(n.func.value, ast.BinOp)
                      and isinstance(n.func.value.right, ast.Constant)
                      and n.func.value.right.value == "configuration.yaml"):
                    keys.add(self.config_key(module, n.args[0]))
        if len(keys) != 1:
            raise LookupError(harness + ": not exactly one configuration")
        return keys.pop()

    def config_key(self, module, expr):
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            module, expr = self.table(module)[2].get(expr.value.id), ast.Name(expr.attr)
        node = self.table(module)[0].get(expr.id) if module and isinstance(expr, ast.Name) else None
        value = node.value if isinstance(node, ast.Assign) else None
        if (isinstance(value, ast.Subscript) and ast.unparse(value.value) == "core_lane_host.CONFIGS"
                and isinstance(value.slice, ast.Constant) and value.slice.value in core_lane_host.CONFIGS):
            return value.slice.value
        raise LookupError(str(module) + ": unresolved configuration " + ast.unparse(expr))

    def entries(self, found, key):
        """{(module, source): expression} of every dict entry ``key`` in the reached definitions."""
        return {(module, ast.unparse(value)): value for module, _, node in found for n in ast.walk(node)
                if isinstance(n, ast.Dict) for k, value in zip(n.keys, n.values)
                if isinstance(k, ast.Constant) and k.value == key}

    def dependencies(self, group):
        """(Core files, Engineering-owned commands) the group's lane processes depend on."""
        files, engineering = set(), set()
        for module, owned in lg.HARNESSES[group]:
            harness = module.__name__
            found = self.reached(harness)
            files |= configuration_files(core_lane_host.CONFIGS[self.configured(harness)])
            for _, _, node in found:
                for n in ast.walk(node):
                    if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                            and n.value.startswith(("/api", "/auth", "/config/"))):
                        files.update(route(n.value, ROUTES, EXACT_ROUTES))
            for (where, source), expr in self.entries(found, "handler").items():
                for handler in (self.strings(where, arg) for arg in expr.args) if isinstance(expr, ast.Call) \
                        else (self.strings(where, expr),):
                    for value in handler:
                        if value.startswith(ENGINEERING):
                            raise LookupError(where + ": unresolved handler " + source)
                        if value not in CUSTOM:
                            files.update(C + value + suffix for suffix in ("/__init__.py", "/manifest.json",
                                                                           "/config_flow.py"))
            for (where, source), expr in self.entries(found, "type").items():
                for value in DYNAMIC.get((where, source)) or (() if (where, source) in DYNAMIC
                                                             else self.strings(where, expr)):
                    if value.startswith(ENGINEERING):
                        engineering.add(value)
                    else:
                        files.update(route(value, COMMANDS, frozenset(COMMANDS)))
            reached = {(where, name) for where, name, _ in found}
            files.update(path for key, paths in ANCHORED.items() if key in reached for path in paths)
            if owned is not None and "STUB" in owned:
                files.update([CONSTRAINTS, *(DUMPS + name for name in NODE_IDS)])
        return files, engineering

    def uncovered(self, group, listed=LISTED):
        return self.dependencies(group)[0] - listed


def configuration_files(text):
    """The Core files a lane configuration loads: core_config, each integration and the YAML loader."""
    tags = set(re.findall(r"!\w+", text))
    if tags - {"!include"}:
        raise LookupError("unmapped configuration tag: " + ", ".join(sorted(tags)))
    files = {YAML_LOADER} if tags else set()
    for key in re.findall(r"^([a-z_]+):", text, re.MULTILINE):
        files.update((CORE_CONFIG,) if key == "homeassistant" else (C + key + "/__init__.py", C + key + "/manifest.json"))
    return files


def disclosed(group):
    return {path for path, (groups, _) in GAPS.items() if group in groups}


class ReadListCoverageTests(unittest.TestCase):
    lanes = Lanes()

    def probe(self, harness, body):
        """A Lanes whose harness has one more top-level definition."""
        return Lanes({harness: (SCRIPTS / (harness + ".py")).read_text() + "\n\n" + body + "\n"})

    def test_the_seven_lane_groups_are_the_harness_map(self):
        self.assertEqual(set(lg.HARNESSES), ALL)
        self.assertEqual(len(ALL), 7)

    def test_every_lane_dependency_is_listed_or_a_disclosed_gap(self):
        for group in sorted(ALL):
            with self.subTest(group=group):
                self.assertEqual(self.lanes.uncovered(group), disclosed(group))

    def test_each_gap_is_unlisted_and_has_a_disposition(self):
        for path, (groups, disposition) in GAPS.items():
            with self.subTest(path=path):
                self.assertNotIn(path, LISTED)
                self.assertTrue(groups <= ALL and groups)
                self.assertGreater(len(disposition.split()), 10)

    def test_the_derivation_is_not_vacuous(self):
        # Every lane bootstraps its admin; each but helper_value, whose script reaches Core only over
        # REST and through the capability's own providers, also authenticates a WebSocket itself.
        for group in sorted(ALL):
            with self.subTest(group=group):
                files, _ = self.lanes.dependencies(group)
                self.assertTrue({C + "auth/__init__.py", CORE_CONFIG, C + "onboarding/views.py"} <= files)
                self.assertEqual(C + "websocket_api/auth.py" in files, group != "helper_value")
        thread, _ = self.lanes.dependencies("thread_matter_topology")
        self.assertTrue({C + "thread/__init__.py", C + "matter/config_flow.py", C + "matter/helpers.py"} <= thread)
        created, _ = self.lanes.dependencies("created_helper_delete")
        self.assertTrue({YAML_LOADER, C + "config/automation.py", C + "input_number/__init__.py"} <= created)

    def test_engineering_owned_commands_are_covered_by_the_capability_profile(self):
        for group in sorted(ALL):
            with self.subTest(group=group):
                _, engineering = self.lanes.dependencies(group)
                for capability in CAPABILITIES[group]:
                    profile = set(READ_LIST["profiles"][capability])
                    self.assertTrue(profile and profile <= LISTED)
        self.assertTrue(self.lanes.dependencies("security_metadata")[1])

    def test_a_dynamic_admission_matches_its_source(self):
        tree = self.lanes.tree("created_object_contract_acceptance")
        domains = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                       and [ast.unparse(t) for t in node.targets] == ["HELPER_DOMAINS"])
        for verb in ("list", "delete"):
            admitted = DYNAMIC[("created_object_contract_acceptance", "domain + '/" + verb + "'")]
            self.assertEqual(admitted, tuple(domain + "/" + verb for domain in domains))

    def test_anchored_data_cites_its_core_file_and_reviewed_anchors(self):
        for (module, name), paths in ANCHORED.items():
            lines = (SCRIPTS / (module + ".py")).read_text().splitlines()
            at = next(index for index, line in enumerate(lines) if line.startswith(name + " = "))
            self.assertIn(paths[0][len("homeassistant/"):], lines[at - 1])
            keys = ast.literal_eval(ast.parse(lines[at]).body[0].value.keys[0])
            self.assertIn(keys, REVIEWED_CORE_BINDINGS)
        self.assertEqual(set(NODE_IDS), {path.name for path in (core_lane_host.MATTER_STUB / "nodes").iterdir()})

    def test_a_new_integration_in_a_lane_configuration_is_an_undisclosed_gap(self):
        config = core_lane_host.CONFIGS["security_metadata"] + "otbr:\n"
        with mock.patch.dict(core_lane_host.CONFIGS, {"security_metadata": config}):
            uncovered = Lanes().uncovered("security_metadata")
        self.assertEqual(uncovered - disclosed("security_metadata"), {C + "otbr/__init__.py", C + "otbr/manifest.json"})

    def test_a_dependency_dropped_from_the_list_is_undisclosed(self):
        for path in (C + "websocket_api/auth.py", C + "config/entity_registry.py", CORE_CONFIG):
            with self.subTest(path=path):
                uncovered = self.lanes.uncovered("created_helper_delete", LISTED - {path})
                self.assertEqual(uncovered - disclosed("created_helper_delete"), {path})

    def test_a_new_core_handler_is_an_undisclosed_gap(self):
        lanes = self.probe("core_device_configuration_local", "def probe():\n    return {'handler': 'otbr'}")
        uncovered = lanes.uncovered("device_configuration_entity")
        self.assertEqual(uncovered - disclosed("device_configuration_entity"), {C + "otbr/__init__.py",
                         C + "otbr/manifest.json", C + "otbr/config_flow.py"})

    def test_an_unmappable_send_fails(self):
        for body in ("def probe():\n    return '/api/hamcp443_unmapped'",
                     "def probe():\n    return {'type': 'hamcp443/unmapped'}",
                     "def probe():\n    return {'type': 'config_entries'}",
                     "def probe(prefix):\n    return {'type': prefix + '/list'}",
                     "def probe():\n    return {'handler': HAMCP443_UNDEFINED}",
                     "def probe(flow):\n    return {'handler': flow.domain}"):
            with self.subTest(body=body), self.assertRaises(LookupError):
                self.probe("core_security_metadata_local", body).dependencies("security_metadata")

    def test_an_unmappable_configuration_fails(self):
        for config in ("homeassistant:\nhttp: !secret http\n", "homeassistant:\nhttp: !env_var HTTP\n"):
            with self.subTest(config=config), mock.patch.dict(core_lane_host.CONFIGS, {"security_metadata": config}), \
                    self.assertRaises(LookupError):
                Lanes().dependencies("security_metadata")
        written = "def probe(owned):\n    (owned / 'configuration.yaml').write_text('default_config:')"
        with self.assertRaises(LookupError):
            self.probe("core_security_metadata_local", written).dependencies("security_metadata")

    def test_an_exact_route_does_not_match_a_longer_one(self):
        self.assertEqual(route("/api/config", ROUTES, EXACT_ROUTES), (C + "api/__init__.py",))
        for value in ("/api/states/light.x", "/api/configx", "/api/config/core"):
            with self.subTest(value=value), self.assertRaises(LookupError):
                route(value, ROUTES, EXACT_ROUTES)


if __name__ == "__main__":
    unittest.main()
