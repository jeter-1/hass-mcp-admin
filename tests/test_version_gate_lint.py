"""Exact-version literal lint for the Engineering runtime (HAMCP-456, G1 F2).

Every exact Core-like (``20YY.M.P``) or ha-mcp-like (``5..19.Y.Z``) version
inside a string constant under ``hass_mcp_engineering_beta/ha_mcp_engineering/``
must be a version of a non-empty slot in ``ci/support-window.yaml`` or be listed
in ``ci/version-gate-allowlist.json``. A listed entry names the file and the
enclosing symbol, the exact count of each literal there, a reason and an expiry
note. The lint fails on an unlisted literal, a changed count, a stale entry, an
entry for a literal now inside the window, and an entry without a reason or an
expiry, so a support-window rotation surfaces every pin it leaves behind.

Scope: string constants in runtime Python only. Docstrings (bare string
statements), comments, two-part prefixes such as ``2026.9`` and the runtime's
JSON registries and descriptors are out of scope. Offline: it reads committed
source only and never imports the runtime.
"""

from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.append(str(ROOT / "tests"))
from support.support_window_matrix import support_window  # noqa: E402

RUNTIME = "hass_mcp_engineering_beta/ha_mcp_engineering"
ALLOWLIST = "ci/version-gate-allowlist.json"
ENTRY_KEYS = {"path", "symbol", "literals", "finding", "reason", "expiry"}
# A version may follow a non-word character, an underscore or a ``v`` tag
# prefix, and may carry a pre-release suffix (which makes it a different exact
# version); a fourth numeric component (an address, a build) is not a version.
_START = r"(?:(?<![A-Za-z0-9_.])|(?<=(?<![A-Za-z0-9_.])v)|(?<=_))"
_TAIL = r"(?:(?:a|b|rc|\.dev)\d+)?(?![A-Za-z0-9]|\.\d)"
PATTERNS = {
    "core": re.compile(_START + r"(20\d\d\.\d{1,2}\.\d{1,3}" + _TAIL + ")"),
    "ha_mcp": re.compile(_START + r"((?:[5-9]|1\d)\.\d{1,3}\.\d{1,3}" + _TAIL + ")"),
}
# A dict element of a table is named by its identity value, so two rows with the
# same keys stay separate entries.
IDENTITY_KEYS = ("capability", "tool")


def window_versions(window, slots=("current", "candidate", "rollback")):
    """Every version a non-empty window slot names, per component."""

    return {component: frozenset(window[component][slot]["version"] for slot in slots
                                 if window[component][slot] is not None)
            for component in PATTERNS}


class _Scanner(ast.NodeVisitor):
    def __init__(self, window):
        self.window, self.stack, self.hits = window, [], []

    def _scoped(self, name, nodes):
        self.stack.append(name)
        for node in nodes:
            self.visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._scoped(node.name, ast.iter_child_nodes(node))

    visit_AsyncFunctionDef = visit_ClassDef = visit_FunctionDef

    def visit_Assign(self, node):
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if names:
            self._scoped(names[0], ast.iter_child_nodes(node))
        else:
            self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name):
            self._scoped(node.target.id, ast.iter_child_nodes(node))
        else:
            self.generic_visit(node)

    def visit_Expr(self, node):
        if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            self.generic_visit(node)

    def visit_Dict(self, node):
        constants = {key.value: value.value for key, value in zip(node.keys, node.values)
                     if isinstance(key, ast.Constant) and isinstance(key.value, str)
                     and isinstance(value, ast.Constant) and isinstance(value.value, str)}
        identity = next((constants[key] for key in IDENTITY_KEYS if key in constants), None)
        if identity is not None:
            self.stack.append(identity)
        for key, value in zip(node.keys, node.values):
            if key is not None:
                self.visit(key)
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.isidentifier():
                self._scoped(key.value, [value])
            else:
                self.visit(value)
        if identity is not None:
            self.stack.pop()

    def visit_Constant(self, node):
        if not isinstance(node.value, str):
            return
        for component, pattern in PATTERNS.items():
            for match in pattern.finditer(node.value):
                if match.group(1) not in self.window[component]:
                    self.hits.append((".".join(self.stack) or "<module>", match.group(1), node.lineno))


def scan_source(path, text, window):
    """The out-of-window literals of one file as (path, symbol, literal, line)."""

    scanner = _Scanner(window)
    scanner.visit(ast.parse(text, filename=path))
    return [(path, symbol, literal, line) for symbol, literal, line in scanner.hits]


def scan_tree(root, window):
    hits = []
    for path in sorted(Path(root, RUNTIME).rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        hits += scan_source(relative, path.read_text(encoding="utf-8"), window)
    return hits


def grouped(hits):
    groups = {}
    for path, symbol, literal, _line in hits:
        groups.setdefault((path, symbol), Counter())[literal] += 1
    return groups


def load_allowlist(text):
    """The allow-list as {(path, symbol): entry}; schema problems are returned, not raised."""

    value = json.loads(text)
    problems, entries = [], {}
    if not (isinstance(value, dict) and set(value) == {"schema_version", "scope", "entries"}
            and value["schema_version"] == 1 and value["scope"] == RUNTIME
            and isinstance(value["entries"], list)):
        return {}, ["allow-list schema: schema_version 1, scope " + RUNTIME + " and an entries list"]
    for index, entry in enumerate(value["entries"]):
        name = f"entry {index}"
        if not (isinstance(entry, dict) and set(entry) == ENTRY_KEYS
                and all(isinstance(entry[key], str) for key in ENTRY_KEYS - {"literals"})):
            problems.append(f"{name}: keys must be exactly {sorted(ENTRY_KEYS)} with string values")
            continue
        name = f"{entry['path']} {entry['symbol']}"
        literals = entry["literals"]
        if not (isinstance(literals, dict) and literals
                and all(isinstance(k, str) and type(v) is int and v > 0 for k, v in literals.items())):
            problems.append(f"{name}: literals must map each version to a positive count")
            continue
        for key in ("reason", "expiry"):
            if not entry[key].strip():
                problems.append(f"{name}: empty {key}")
        key = (entry["path"], entry["symbol"])
        if key in entries:
            problems.append(f"{name}: listed twice")
        entries[key] = entry
    return entries, problems


def check(hits, entries, window):
    """Every way the scanned tree and the allow-list disagree, as readable lines."""

    problems = []
    found = grouped(hits)
    lines = {}
    for path, symbol, literal, line in hits:
        lines.setdefault((path, symbol), []).append(f"{literal}@{line}")
    for key, entry in sorted(entries.items()):
        inside = sorted(literal for literal in entry["literals"]
                        if any(literal in window[component] for component in PATTERNS))
        if inside:
            problems.append(f"{key[0]} {key[1]}: {', '.join(inside)} now inside the support window; "
                            "drop it from the entry")
        if key not in found:
            problems.append(f"{key[0]} {key[1]}: stale entry, no out-of-window literal remains")
    for key, counts in sorted(found.items()):
        entry = entries.get(key)
        if entry is None:
            problems.append(f"{key[0]} {key[1]}: unlisted {', '.join(lines[key])}; list it with a reason "
                            f"and an expiry, or bring it inside the window: "
                            + json.dumps({"path": key[0], "symbol": key[1], "literals": dict(sorted(counts.items()))}))
        elif dict(counts) != entry["literals"]:
            problems.append(f"{key[0]} {key[1]}: counts {dict(sorted(counts.items()))} differ from the "
                            f"listed {dict(sorted(entry['literals'].items()))} ({', '.join(lines[key])})")
    return problems


class VersionGateLintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.window = window_versions(support_window().load(ROOT))

    def test_runtime_literals_match_the_allowlist(self):
        entries, problems = load_allowlist(Path(ROOT, ALLOWLIST).read_text(encoding="utf-8"))
        problems += check(scan_tree(ROOT, self.window), entries, self.window)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_seeded_findings_keep_their_expiry(self):
        entries, _ = load_allowlist(Path(ROOT, ALLOWLIST).read_text(encoding="utf-8"))
        expiries = {entry["finding"]: set() for entry in entries.values()}
        for entry in entries.values():
            expiries[entry["finding"]].add(entry["expiry"])
        self.assertEqual(expiries.get("F2-1"), {"R1"})
        self.assertEqual(expiries.get("F2-3"), {"2.9.1 cleanup"})
        # F2-5 (the stale history_slices label) closed with the native history read (HAMCP-511).
        self.assertNotIn("F2-5", expiries)

    def test_no_entry_awaits_an_owner_decision(self):
        text = Path(ROOT, ALLOWLIST).read_text(encoding="utf-8")
        entries, _ = load_allowlist(text)
        pending = sorted(" ".join(key) for key, entry in entries.items() if "[JOSH]" in entry["expiry"])
        self.assertEqual(pending, [], "\n".join(pending))
        self.assertNotIn("[JOSH]", text)

    def test_allowlist_name_stays_outside_the_core_evidence_trigger(self):
        self.assertFalse(Path(ALLOWLIST).name.startswith("core-"))


WINDOW = {"core": frozenset({"2026.11.0"}), "ha_mcp": frozenset({"9.2.0"})}


def entry(path, symbol, literals, finding="history", reason="kept row", expiry="later"):
    return {"path": path, "symbol": symbol, "literals": literals, "finding": finding,
            "reason": reason, "expiry": expiry}


def allowlist(*entries):
    return json.dumps({"schema_version": 1, "scope": RUNTIME, "entries": list(entries)})


class ScannerTests(unittest.TestCase):
    def scan(self, text):
        return [(symbol, literal) for _path, symbol, literal, _line in scan_source("m.py", text, WINDOW)]

    def test_window_versions_pass_and_others_hit(self):
        self.assertEqual(self.scan('A = ("2026.11.0", "9.2.0", "2026.9.4", "8.4.3")\n'),
                         [("A", "2026.9.4"), ("A", "8.4.3")])

    def test_tag_prefix_underscore_and_prerelease_are_versions(self):
        text = 'A = "ha-mcp-v9.1.2-x exact_legacy_2026.9.2 9.2.0rc1 2026.11.0b3"\n'
        self.assertEqual(self.scan(text), [("A", "2026.9.2"), ("A", "2026.11.0b3"), ("A", "9.1.2"),
                                           ("A", "9.2.0rc1")])

    def test_non_versions_are_ignored(self):
        text = 'A = "10.0.0.1 2026.9 dev8.4.3 a9.1.0 1.2.3 2.4.2 9.1.2x"\n'
        self.assertEqual(self.scan(text), [])

    def test_docstrings_are_ignored_but_other_strings_are_not(self):
        text = '"""Module 8.4.3."""\ndef f():\n    """Uses 8.4.3."""\n    return "8.5.0"\n'
        self.assertEqual(self.scan(text), [("f", "8.5.0")])

    def test_symbols_follow_classes_functions_assignments_keys_and_identity(self):
        text = (
            "class C:\n    def m(self):\n        x = {'k': '8.4.3'}\n"
            "T = ({'capability': 'one', 'note': '8.5.0'}, {'capability': 'two', 'note': '8.6.0'})\n"
            "U: dict = {'tool': 't', 'v': ['9.1.0']}\n"
        )
        self.assertEqual(self.scan(text), [("C.m.x.k", "8.4.3"), ("T.one.note", "8.5.0"),
                                           ("T.two.note", "8.6.0"), ("U.t.v", "9.1.0")])

    def test_window_versions_cover_every_non_empty_slot(self):
        window = {"core": {"current": {"version": "2026.11.0"}, "candidate": None,
                           "rollback": {"version": "2026.10.2"}},
                  "ha_mcp": {"current": {"version": "9.2.0"}, "candidate": {"version": "9.3.0"},
                             "rollback": None}}
        self.assertEqual(window_versions(window), {"core": frozenset({"2026.11.0", "2026.10.2"}),
                                                   "ha_mcp": frozenset({"9.2.0", "9.3.0"})})


class CheckTests(unittest.TestCase):
    TEXT = 'A = ("8.4.3", "8.4.3", "2026.9.4")\n'

    def problems(self, text, *entries):
        listed, problems = load_allowlist(allowlist(*entries))
        return problems + check(scan_source("m.py", text, WINDOW), listed, WINDOW)

    def test_exact_listing_passes(self):
        self.assertEqual(self.problems(self.TEXT, entry("m.py", "A", {"8.4.3": 2, "2026.9.4": 1})), [])

    def test_unlisted_literal_fails_with_a_paste_ready_entry(self):
        problems = self.problems(self.TEXT)
        self.assertEqual(len(problems), 1)
        self.assertIn("m.py A: unlisted 8.4.3@1, 8.4.3@1, 2026.9.4@1", problems[0])
        self.assertIn('"literals": {"2026.9.4": 1, "8.4.3": 2}', problems[0])

    def test_count_drift_fails(self):
        problems = self.problems(self.TEXT, entry("m.py", "A", {"8.4.3": 1, "2026.9.4": 1}))
        self.assertEqual(len(problems), 1)
        self.assertIn("differ from the listed", problems[0])

    def test_new_literal_in_a_listed_symbol_fails(self):
        problems = self.problems(self.TEXT, entry("m.py", "A", {"8.4.3": 2}))
        self.assertEqual(len(problems), 1)
        self.assertIn("differ from the listed", problems[0])

    def test_stale_entry_fails(self):
        problems = self.problems('A = "9.2.0"\n', entry("m.py", "A", {"8.4.3": 1}))
        self.assertEqual(problems, ["m.py A: stale entry, no out-of-window literal remains"])

    def test_entry_for_a_window_version_fails(self):
        problems = self.problems('A = ("9.2.0", "8.4.3")\n', entry("m.py", "A", {"8.4.3": 1, "9.2.0": 1}))
        self.assertIn("m.py A: 9.2.0 now inside the support window; drop it from the entry", problems)

    def test_window_rotation_surfaces_old_pins(self):
        rotated = {"core": frozenset({"2026.11.0"}), "ha_mcp": frozenset({"9.3.0"})}
        hits = scan_source("m.py", 'A = "9.2.0"\n', rotated)
        self.assertEqual(len(check(hits, {}, rotated)), 1)

    def test_empty_reason_or_expiry_fails(self):
        problems = self.problems(self.TEXT, entry("m.py", "A", {"8.4.3": 2, "2026.9.4": 1}, reason=" ",
                                                  expiry=""))
        self.assertEqual(problems, ["m.py A: empty reason", "m.py A: empty expiry"])

    def test_duplicate_and_malformed_entries_fail(self):
        good = entry("m.py", "A", {"8.4.3": 2, "2026.9.4": 1})
        self.assertIn("m.py A: listed twice", self.problems(self.TEXT, good, good))
        bad = dict(good, extra="x")
        self.assertTrue(self.problems(self.TEXT, bad)[0].startswith("entry 0: keys must be exactly"))
        zero = entry("m.py", "A", {"8.4.3": 0})
        self.assertIn("m.py A: literals must map each version to a positive count", self.problems(self.TEXT, zero))

    def test_wrong_scope_or_schema_fails(self):
        _, problems = load_allowlist(json.dumps({"schema_version": 1, "scope": "x", "entries": []}))
        self.assertEqual(len(problems), 1)
        _, problems = load_allowlist(json.dumps({"schema_version": 2, "scope": RUNTIME, "entries": []}))
        self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()
